"""EDL: settings + facts (energy, words, retake decisions) → concrete edit decisions.

Owns THE source→output time map. Everything downstream (captions, SFX times,
CTA window, hook plan) reads times from here. Pure and fast — recomputed on
every render so tunables never require re-analysis.

Key invariants proven in the manual session:
- Audio spans stay CONTINUOUS across punch-in zoom cuts (video-only cuts),
  so mid-phrase zooms can't clip words.
- Keep-intervals are expanded to fully cover any whisper word they touch,
  which prevents boundary clipping ("old web way" / "catch up later" bugs).
"""
from __future__ import annotations

from typing import Optional

from ..models import BrainResult, RenderSettings

WIN_S = 0.05

HOOK_WINDOW_S = 3.0
TITLE_CARD_S = 0.8
LONG_SEGMENT_PUNCH_S = 8.0


def _speech_regions(env: list[float], floor_db: float) -> list[list[float]]:
    """Contiguous above-floor runs of the 50ms envelope, merged across <=150ms dips."""
    regions: list[list[float]] = []
    start: Optional[int] = None
    for i, db in enumerate(env):
        if db > floor_db:
            if start is None:
                start = i
        else:
            if start is not None:
                regions.append([start * WIN_S, i * WIN_S])
                start = None
    if start is not None:
        regions.append([start * WIN_S, len(env) * WIN_S])
    merged: list[list[float]] = []
    for r in regions:
        if merged and r[0] - merged[-1][1] <= 0.15:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] >= 0.1]


def _subtract_spans(intervals: list[list[float]], drops: list[list[float]]) -> list[list[float]]:
    out = []
    for iv in intervals:
        pieces = [iv[:]]
        for d in drops:
            nxt = []
            for p in pieces:
                if d[1] <= p[0] or d[0] >= p[1]:
                    nxt.append(p)
                    continue
                if d[0] > p[0]:
                    nxt.append([p[0], d[0]])
                if d[1] < p[1]:
                    nxt.append([d[1], p[1]])
            pieces = nxt
        out.extend(p for p in pieces if p[1] - p[0] >= 0.15)
    return out


def _nearest_kept_out(t: float, audio_segs: list[dict]) -> Optional[float]:
    """Nearest surviving keep-interval boundary's output time — fallback for
    an overlay anchor whose source time fell inside a dropped retake span."""
    best_out, best_dist = None, None
    for seg in audio_segs:
        for src_bound, out_bound in ((seg["src0"], seg["out0"]),
                                     (seg["src1"], seg["out0"] + (seg["src1"] - seg["src0"]))):
            dist = abs(t - src_bound)
            if best_dist is None or dist < best_dist:
                best_dist, best_out = dist, out_bound
    return best_out


MIN_OVERLAY_ON_SCREEN_S = 0.4


def _caption_span_for_line(chunks: list[dict], line_out_t0: Optional[float],
                           line_out_t1: Optional[float], start_out: float) -> Optional[tuple[float, float]]:
    """Given a cue's already-resolved start_out and its aligned dialogue line's
    window mapped into OUTPUT time, find the caption-chunk span covering that
    line: from the chunk containing (or immediately following) the anchor,
    through the last chunk of the line. Returns None if there's nothing to
    snap to (unmatched line, anchor/line fell entirely outside kept audio, no
    overlapping chunks) — callers must degrade to the un-snapped window."""
    if line_out_t0 is None or line_out_t1 is None or line_out_t1 <= line_out_t0:
        return None
    line_chunks = [c for c in chunks if c["t0"] < line_out_t1 - 1e-3 and c["t1"] > line_out_t0 + 1e-3]
    if not line_chunks:
        return None
    anchor_chunk = next((c for c in line_chunks if c["t1"] > start_out), line_chunks[-1])
    span_start = anchor_chunk["t0"]
    span_end = line_chunks[-1]["t1"]
    if span_end <= span_start:
        return None
    return span_start, span_end


def _dedupe_overlapping_events(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """Guarantees only one script-driven overlay is ever on screen at a time.
    Multiple cues can legitimately anchor close together (or land on top of
    each other after caption-snapping drift even when script_align already
    spread out same-line ties) -- without this their windows can visually
    collide. Walks events in start_out order; when the next one would start
    before the current one ends, truncates the current one's end_out to the
    next one's start_out, dropping it entirely if that leaves less than
    MIN_OVERLAY_ON_SCREEN_S on screen."""
    ordered = sorted(events, key=lambda e: e["start_out"])
    kept: list[dict] = []
    dropped: list[dict] = []
    for ev in ordered:
        if kept and ev["start_out"] < kept[-1]["end_out"]:
            kept[-1] = {**kept[-1], "end_out": round(ev["start_out"], 3)}
            if kept[-1]["end_out"] - kept[-1]["start_out"] < MIN_OVERLAY_ON_SCREEN_S:
                removed = kept.pop()
                dropped.append({"cue_id": removed["cue_id"], "reason": "collided with a later overlay"})
        kept.append(ev)
    return kept, dropped


def build_edl(*, words: list[dict], energy: dict, brain: BrainResult,
              settings: RenderSettings, source_duration: float,
              variant: str = "hook_a",
              boundary_padding: Optional[dict[int, float]] = None,
              overlay_cues: Optional[list[dict]] = None) -> dict:
    """boundary_padding: {interval_index: extra_s} — QC retry widens boundaries."""
    s = settings
    env = energy["db"]
    regions = _speech_regions(env, s.cuts.silence_floor_db)

    # pad + merge regions whose gap is under the keep threshold
    pad = s.cuts.pad_ms / 1000.0
    min_gap = s.cuts.min_pause_kept_ms / 1000.0
    padded = [[max(0.0, r[0] - pad), min(source_duration, r[1] + pad)] for r in regions]
    keep: list[list[float]] = []
    for r in padded:
        if keep and r[0] - keep[-1][1] <= min_gap:
            keep[-1][1] = max(keep[-1][1], r[1])
        else:
            keep.append(r)

    # drop rejected takes
    drop_spans: list[list[float]] = []
    if s.cuts.retake_removal:
        for rt in brain.retakes:
            drop_spans.extend([list(d) for d in rt.drop_spans])
    if drop_spans:
        keep = _subtract_spans(keep, drop_spans)

    # never clip a word: expand intervals to cover overlapped words entirely
    # (words inside dropped takes were removed with their span)
    for iv in keep:
        for w in words:
            if w["t1"] > iv[0] and w["t0"] < iv[1]:
                in_drop = any(d[0] <= w["t0"] and w["t1"] <= d[1] + 0.05 for d in drop_spans)
                if not in_drop:
                    iv[0] = min(iv[0], max(0.0, w["t0"] - 0.05))
                    iv[1] = max(iv[1], min(source_duration, w["t1"] + 0.05))
    # guarantee: every word not inside a dropped take is covered by an interval
    for w in words:
        in_drop = any(d[0] - 0.05 <= w["t0"] and w["t1"] <= d[1] + 0.05 for d in drop_spans)
        if in_drop:
            continue
        covered = any(iv[0] <= w["t0"] and w["t1"] <= iv[1] for iv in keep)
        if not covered:
            keep.append([max(0.0, w["t0"] - pad), min(source_duration, w["t1"] + pad)])

    # expansion can create overlaps — merge them
    merged: list[list[float]] = []
    for iv in sorted(keep):
        if merged and iv[0] <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], iv[1])
        else:
            merged.append(iv)
    keep = merged

    if boundary_padding:
        for idx, extra in boundary_padding.items():
            if 0 <= idx < len(keep):
                keep[idx][0] = max(0.0, keep[idx][0] - extra)
                keep[idx][1] = min(source_duration, keep[idx][1] + extra)

    # ---- time map: audio segments (continuous) ----
    lead_in = TITLE_CARD_S if variant == "hook_b" else 0.0
    audio_segs = []
    t_out = lead_in
    for iv in keep:
        audio_segs.append({"src0": iv[0], "src1": iv[1], "out0": t_out})
        t_out += iv[1] - iv[0]
    total_out = t_out

    def src_to_out(t: float) -> Optional[float]:
        for seg in audio_segs:
            if seg["src0"] - 0.05 <= t <= seg["src1"] + 0.05:
                return seg["out0"] + min(max(t - seg["src0"], 0.0), seg["src1"] - seg["src0"])
        return None

    # ---- video segments: audio cuts + video-only punch cuts within long spans ----
    video_cut_srcs: list[list[float]] = []
    for iv in keep:
        pieces = [iv[:]]
        while pieces[-1][1] - pieces[-1][0] > LONG_SEGMENT_PUNCH_S:
            a, b = pieces[-1]
            mid = a + LONG_SEGMENT_PUNCH_S * 0.75
            word_bounds = [w["t1"] for w in words if a + 1.0 < w["t1"] < b - 1.0]
            cut_at = min(word_bounds, key=lambda t: abs(t - mid)) if word_bounds else mid
            if cut_at - a < 1.0 or b - cut_at < 1.0:
                break
            pieces[-1] = [a, cut_at]
            pieces.append([cut_at, b])
        video_cut_srcs.extend(pieces)

    # zoom every Nth video segment: high=alternating, medium=every 3rd, low=every 5th
    period = {"high": 2, "medium": 3, "low": 5}[s.zoom.frequency]
    video_segs = []
    for i, (a, b) in enumerate(video_cut_srcs):
        zoomed = s.zoom.enabled and (i % period == period - 1)
        video_segs.append({"src0": a, "src1": b,
                           "zoom": round(s.zoom.level if zoomed else 1.0, 3)})

    # hook punch: force a zoom on the very first video segment
    if s.zoom.enabled and s.zoom.hook_punch and video_segs:
        video_segs[0]["zoom"] = round(max(s.zoom.level + 0.05, 1.1), 3)
        if len(video_segs) > 1:
            video_segs[1]["zoom"] = 1.0

    # ---- caption chunks in OUTPUT time ----
    out_words = []
    for i, w in enumerate(words):
        if src_to_out((w["t0"] + w["t1"]) / 2) is None:
            continue  # word's core is not in any kept interval
        o0, o1 = src_to_out(w["t0"]), src_to_out(w["t1"])
        if o0 is None:
            o0 = max(0.0, (o1 or 0) - (w["t1"] - w["t0"]))
        if o1 is None:
            o1 = o0 + (w["t1"] - w["t0"])
        out_words.append({"w": w["w"], "t0": round(o0, 3), "t1": round(o1, 3), "src_index": i})

    keyword_idxs = {k.word_index for k in brain.keywords} if s.captions.highlight_keywords else set()
    chunks = _chunk_words(out_words, s, keyword_idxs)

    # ---- sfx / overlays ----
    cut_times_out = []
    acc: dict[float, bool] = {}
    for seg in video_segs[1:]:
        t = src_to_out(seg["src0"] + 0.01)
        if t is not None and t > 0.2 and t not in acc:
            cut_times_out.append(round(t, 3))
            acc[t] = True

    cta_start = max(lead_in, total_out - s.overlays.cta_last_seconds) if s.overlays.cta_enabled else None

    # ---- script-driven overlay/effect events (source anchor -> output window) ----
    overlay_events: list[dict] = []
    dropped_overlay_events: list[dict] = []
    for cue in (overlay_cues or []):
        t = cue["anchor_src_t"]
        if t is None:
            dropped_overlay_events.append({"cue_id": cue["cue_id"], "reason": "no source anchor"})
            continue
        start_out = src_to_out(t)
        if start_out is None:
            start_out = _nearest_kept_out(t, audio_segs)
        if start_out is None:
            dropped_overlay_events.append({"cue_id": cue["cue_id"], "reason": "anchor not in any kept interval"})
            continue

        end_out = min(start_out + cue["duration_s"], total_out)

        # snap to the caption-chunk span of the cue's aligned line, if
        # resolvable — a pure refinement on top of the already-resolved
        # start_out; drop-span relocation above always runs first and takes
        # precedence, this step only adjusts within whatever start_out that
        # produced
        line_src_t0, line_src_t1 = cue.get("line_src_t0"), cue.get("line_src_t1")
        line_out_t0 = src_to_out(line_src_t0) if line_src_t0 is not None else None
        line_out_t1 = src_to_out(line_src_t1) if line_src_t1 is not None else None
        span = _caption_span_for_line(chunks, line_out_t0, line_out_t1, start_out)
        if span is not None:
            snapped_start, snapped_line_end = span
            snapped_end = min(snapped_line_end, snapped_start + cue["duration_s"])
            snapped_end = max(snapped_end, snapped_start + MIN_OVERLAY_ON_SCREEN_S)
            start_out, end_out = snapped_start, min(snapped_end, total_out)

        overlay_events.append({
            "cue_id": cue["cue_id"], "kind": cue.get("kind", "overlay"),
            "start_out": round(start_out, 3), "end_out": round(end_out, 3),
            "spec": cue.get("spec", {}),
        })

    overlay_events, collided = _dedupe_overlapping_events(overlay_events)
    dropped_overlay_events.extend(collided)

    return {
        "variant": variant,
        "lead_in_s": lead_in,
        "total_out_s": round(total_out, 3),
        "audio_segments": audio_segs,
        "video_segments": video_segs,
        "caption_chunks": chunks,
        "out_words": out_words,
        "cut_times_out": cut_times_out,
        "cta_start": cta_start,
        "keep_intervals": keep,
        "expected_words": [w["w"] for w in out_words],
        "banner_text": (settings.overlays.banner_text.strip() or brain.banner_text),
        "cta_text": (settings.overlays.cta_text.strip() or brain.cta_text),
        "hook_texts": brain.hooks.model_dump(),
        "overlay_events": overlay_events,
        "dropped_overlay_events": dropped_overlay_events,
    }


def _chunk_words(out_words: list[dict], s: RenderSettings, keyword_idxs: set[int]) -> list[dict]:
    import re
    chunks: list[dict] = []
    cur: list[dict] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append({
                "t0": cur[0]["t0"],
                "words": [
                    {"text": w["w"], "highlight": w["src_index"] in keyword_idxs}
                    for w in cur
                ],
                "t_last_end": cur[-1]["t1"],
            })
            cur, cur_len = [], 0

    max_words = s.captions.max_words_per_chunk
    max_chars = s.captions.max_chars_per_chunk
    for i, w in enumerate(out_words):
        wl = len(w["w"])
        if cur and (len(cur) >= max_words or cur_len + 1 + wl > max_chars):
            flush()
        cur.append(w)
        cur_len += (1 if cur_len else 0) + wl
        gap = out_words[i + 1]["t0"] - w["t1"] if i + 1 < len(out_words) else 99.0
        if re.search(r"[\.\?!,]$", w["w"]) or gap > 0.7:
            flush()
    flush()
    # display end = next chunk start (continuous), capped
    for i, ch in enumerate(chunks):
        if i + 1 < len(chunks):
            ch["t1"] = round(min(chunks[i + 1]["t0"], ch["t_last_end"] + 1.1), 3)
        else:
            ch["t1"] = round(ch["t_last_end"] + 0.6, 3)
        del ch["t_last_end"]
    return chunks
