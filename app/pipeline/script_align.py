"""Aligns a parsed script's dialogue lines against the REAL whisper transcript
of the actual recording (words.json / speech_segments.json), then anchors each
ON-SCREEN/OVERLAY/EFFECT cue to a real SOURCE-time point. Must run after brain
(needs brain.retakes[].drop_spans to avoid anchoring on a take that gets cut).

Only produces source-time anchors — never reimplements edl.py's source→output
mapping (src_to_out lives inside build_edl(), which needs full per-variant
settings)."""
from __future__ import annotations

import difflib
import re
from typing import Optional

from pydantic import BaseModel

from ..models import BrainResult
from .script_parse import ScriptCue, ScriptDoc, ScriptLine
from .transcribe import norm_token

MATCH_FLOOR = 0.45
RETAKE_COVERAGE_FLOOR = 0.8
GLOBAL_FALLBACK_UNMATCHED_RATIO = 0.30
LOOKBACK = 2

MIN_AVAILABLE_DURATION_S = 0.4
# catalog's longest default_duration_s is 3.5s; 6.5s gives bespoke real
# headroom for genuinely multi-stage content (a 4-8 beat animation needs
# ~0.8-1.5s per beat to register) without ever handing a cue a full 7-9s
# dialogue-line budget (reads as too slow for short-form pacing even if the
# line itself runs that long) -- the gap-to-next-cue cap below is what
# actually prevents overlap, this is just an outer sanity ceiling.
MAX_AVAILABLE_DURATION_S = 6.5
DEFAULT_AVAILABLE_DURATION_S = 2.0


class AlignedLine(BaseModel):
    index: int
    matched: bool
    t0: Optional[float] = None
    t1: Optional[float] = None
    confidence: float = 0.0


class AlignedCue(BaseModel):
    index: int
    kind: str
    anchor_src_t: Optional[float] = None
    anchor_line_index: Optional[int] = None
    confidence: float = 0.0


class AlignmentResult(BaseModel):
    lines: list[AlignedLine] = []
    cues: list[AlignedCue] = []


def _tokenize(text: str) -> list[str]:
    return [t for t in (norm_token(w) for w in text.split()) if t]


def _retake_covered(sentence: dict, drop_spans: list[list[float]]) -> bool:
    t0, t1 = sentence["t0"], sentence["t1"]
    dur = t1 - t0
    if dur <= 0:
        return False
    for d0, d1 in drop_spans:
        overlap = max(0.0, min(t1, d1) - max(t0, d0))
        if overlap / dur >= RETAKE_COVERAGE_FLOOR:
            return True
    return False


def align_script(words: list[dict], segments: dict, brain: BrainResult,
                 script_doc: ScriptDoc,
                 manual_overrides: Optional[dict[int, int]] = None) -> AlignmentResult:
    """manual_overrides: {cue.index: dialogue_line.index} — a user-specified
    anchor line for a cue, set via the dashboard's timing control when
    automatic resolution picks the wrong line. Takes precedence over both the
    parse-time attachment (ON-SCREEN cues) and the authored-timestamp-based
    fallback search."""
    sentences = segments["sentences"]
    drop_spans = [d for rt in brain.retakes for d in rt.drop_spans]
    sent_tokens = [_tokenize(s["text"]) for s in sentences]
    covered = [_retake_covered(s, drop_spans) for s in sentences]

    aligned_lines: list[AlignedLine] = []
    cursor = 0
    unmatched = 0
    for li, line in enumerate(script_doc.lines):
        line_tokens = _tokenize(line.text)
        next_tokens = _tokenize(script_doc.lines[li + 1].text) if li + 1 < len(script_doc.lines) else []
        best_idx, best_score = None, 0.0
        for j in range(max(0, cursor - LOOKBACK), len(sentences)):
            if covered[j] or not sent_tokens[j]:
                continue
            score = difflib.SequenceMatcher(
                a=line_tokens, b=sent_tokens[j], autojunk=False).ratio()
            if score > best_score:
                best_score, best_idx = score, j
        if best_idx is not None and best_score >= MATCH_FLOOR:
            # a script line may span >1 whisper sentence (comma run-ons split
            # by pause) — greedily absorb trailing same-breath sentences that
            # still fit this line better than they'd fit the next one
            end_idx = best_idx
            while end_idx + 1 < len(sentences) and not covered[end_idx + 1] and \
                    sentences[end_idx + 1]["t0"] - sentences[end_idx]["t1"] < 1.0:
                cand = sent_tokens[end_idx + 1]
                score_this = difflib.SequenceMatcher(a=line_tokens, b=cand, autojunk=False).ratio()
                score_next = difflib.SequenceMatcher(a=next_tokens, b=cand, autojunk=False).ratio() if next_tokens else 0.0
                if score_this >= score_next:
                    end_idx += 1
                else:
                    break
            aligned_lines.append(AlignedLine(
                index=line.index, matched=True, t0=sentences[best_idx]["t0"],
                t1=sentences[end_idx]["t1"], confidence=round(best_score, 3)))
            cursor = end_idx + 1
        else:
            aligned_lines.append(AlignedLine(
                index=line.index, matched=False, confidence=round(best_score, 3)))
            unmatched += 1

    if script_doc.lines and unmatched / len(script_doc.lines) > GLOBAL_FALLBACK_UNMATCHED_RATIO:
        aligned_lines = _global_align(words, drop_spans, script_doc.lines)

    aligned_cues = _align_cues(script_doc, aligned_lines, sentences, words, manual_overrides)
    return AlignmentResult(lines=aligned_lines, cues=aligned_cues)


def cue_available_durations(alignment: AlignmentResult,
                            window_overrides: Optional[dict[int, tuple[float, float]]] = None
                            ) -> dict[int, float]:
    """Real per-cue on-screen time budget (SOURCE-time seconds). Formula: time
    remaining from the anchor point to the end of its "home" window, further
    capped by the gap to the NEXT cue's own anchor (so two cues anchored close
    together never get overlapping budgets), floored/ceilinged. Never raises;
    falls back to DEFAULT_AVAILABLE_DURATION_S for unmatched/unanchored cues.

    window_overrides: {cue.index: (t0, t1)} -- when the overlay-timing agent
    (overlay_timing_agent.py) resolves a cue to a specific WORD rather than a
    whole dialogue line, its "home" window is the sentence containing that
    word, not the line the heuristic originally matched -- callers pass that
    here instead of relying on the line lookup below."""
    window_overrides = window_overrides or {}
    lines_by_index = {l.index: l for l in alignment.lines}
    cues_sorted = sorted(alignment.cues, key=lambda c: c.index)
    out: dict[int, float] = {}
    for i, cue in enumerate(cues_sorted):
        if cue.anchor_src_t is None:
            out[cue.index] = DEFAULT_AVAILABLE_DURATION_S
            continue
        if cue.index in window_overrides:
            _, t1 = window_overrides[cue.index]
            remaining = t1 - cue.anchor_src_t
        else:
            line = lines_by_index.get(cue.anchor_line_index) if cue.anchor_line_index is not None else None
            if line is not None and line.matched and line.t1 is not None:
                remaining = line.t1 - cue.anchor_src_t
            else:
                remaining = DEFAULT_AVAILABLE_DURATION_S
        next_cue = next((c for c in cues_sorted[i + 1:] if c.anchor_src_t is not None), None)
        if next_cue is not None:
            gap = next_cue.anchor_src_t - cue.anchor_src_t
            if gap > 0:
                remaining = min(remaining, gap)
        out[cue.index] = round(max(MIN_AVAILABLE_DURATION_S, min(remaining, MAX_AVAILABLE_DURATION_S)), 3)
    return out


def _global_align(words: list[dict], drop_spans: list[list[float]],
                  lines: list[ScriptLine]) -> list[AlignedLine]:
    """Heavy-ad-lib fallback: one global sequence match over the whole
    concatenated token streams instead of per-sentence matching."""
    src_tokens: list[str] = []
    src_times: list[tuple[float, float]] = []
    for w in words:
        if any(d[0] <= w["t0"] and w["t1"] <= d[1] for d in drop_spans):
            continue
        tok = norm_token(w["w"])
        if tok:
            src_tokens.append(tok)
            src_times.append((w["t0"], w["t1"]))

    line_tokens: list[str] = []
    line_bounds: list[tuple[int, int]] = []  # [start, end) into line_tokens per line
    for line in lines:
        start = len(line_tokens)
        line_tokens.extend(_tokenize(line.text))
        line_bounds.append((start, len(line_tokens)))

    sm = difflib.SequenceMatcher(a=line_tokens, b=src_tokens, autojunk=False)
    blocks = sm.get_matching_blocks()

    out: list[AlignedLine] = []
    for line, (start, end) in zip(lines, line_bounds):
        span_len = end - start
        matched_src_idxs: list[int] = []
        for a, b, size in blocks:
            if size == 0:
                continue
            ov_start, ov_end = max(a, start), min(a + size, end)
            if ov_end > ov_start:
                offset = ov_start - a
                matched_src_idxs.extend(range(b + offset, b + offset + (ov_end - ov_start)))
        if matched_src_idxs and span_len > 0:
            t0 = src_times[min(matched_src_idxs)][0]
            t1 = src_times[max(matched_src_idxs)][1]
            confidence = len(matched_src_idxs) / span_len
            out.append(AlignedLine(index=line.index, matched=True, t0=t0, t1=t1,
                                   confidence=round(confidence, 3)))
        else:
            out.append(AlignedLine(index=line.index, matched=False, confidence=0.0))
    return out


_QUOTED_RE = re.compile(r"['‘’]([^'‘’]{2,30})['‘’]|\"([^\"]{2,30})\"")


def _align_cues(script_doc: ScriptDoc, aligned_lines: list[AlignedLine],
                sentences: list[dict], words: list[dict],
                manual_overrides: Optional[dict[int, int]] = None) -> list[AlignedCue]:
    manual_overrides = manual_overrides or {}
    by_index = {a.index: a for a in aligned_lines}
    matched_lines = [a for a in aligned_lines if a.matched]

    def line_window(line_idx: int) -> Optional[tuple[float, float]]:
        a = by_index.get(line_idx)
        if a and a.matched:
            return a.t0, a.t1
        return None

    def nearest_matched_time(authored_ts: float) -> Optional[float]:
        if not matched_lines:
            return None
        # nearest by authored-line-order proximity, using script_doc.lines authored_ts
        best, best_dist = None, None
        for a in matched_lines:
            line = script_doc.lines[a.index]
            dist = abs(line.authored_ts - authored_ts)
            if best_dist is None or dist < best_dist:
                best, best_dist = a, dist
        return best.t0 if best else None

    out: list[AlignedCue] = []
    for cue in script_doc.cues:
        manual_line_idx = manual_overrides.get(cue.index)
        if manual_line_idx is not None:
            anchor_line_idx = manual_line_idx
        else:
            anchor_line_idx = cue.anchor_line_index
            if anchor_line_idx is None:
                anchor_line_idx = 0
                for line in script_doc.lines:
                    if line.authored_ts <= cue.authored_ts:
                        anchor_line_idx = line.index
                    else:
                        break
        next_line = next((l for l in script_doc.lines if l.index == anchor_line_idx + 1), None)
        window = line_window(anchor_line_idx)

        anchor_src_t: Optional[float] = None
        confidence = 0.0
        if window is not None:
            t0, t1 = window
            quoted_word = _first_quoted_word(cue.text)
            if quoted_word:
                hit = _find_word_in_window(sentences, words, t0, t1, quoted_word)
                if hit is not None:
                    anchor_src_t = hit
                    confidence = 0.9
            if anchor_src_t is None:
                anchor_line = script_doc.lines[anchor_line_idx]
                span = (next_line.authored_ts - anchor_line.authored_ts) if next_line else max(t1 - t0, 1.0)
                frac = 0.0 if span <= 0 else max(0.0, min(1.0, (cue.authored_ts - anchor_line.authored_ts) / span))
                anchor_src_t = t0 + frac * (t1 - t0)
                confidence = by_index[anchor_line_idx].confidence
        else:
            anchor_src_t = nearest_matched_time(cue.authored_ts)
            confidence = 0.1 if anchor_src_t is not None else 0.0

        # a user-specified anchor line is trusted outright, regardless of
        # whatever score the underlying line match happened to get
        if manual_line_idx is not None and anchor_src_t is not None:
            confidence = 1.0

        out.append(AlignedCue(index=cue.index, kind=cue.kind,
                              anchor_src_t=round(anchor_src_t, 3) if anchor_src_t is not None else None,
                              anchor_line_index=anchor_line_idx,
                              confidence=round(confidence, 3)))
    _respread_colliding_anchors(out, line_window)
    return out


def _respread_colliding_anchors(cues: list["AlignedCue"], line_window) -> None:
    """Multiple ON-SCREEN/OVERLAY/EFFECT cues commonly share one authored
    timestamp in a script (e.g. two B-ROLL ideas listed under the same [0:36]
    marker) -- the fractional-position fallback above then resolves them to
    the EXACT same anchor_src_t, so their on-screen windows fully collide.
    A user-pinned manual anchor is never touched here (it's deliberate).
    Detects same-line groups that actually collided and spaces them evenly
    across the line's real aligned window, preserving relative (cue-index)
    order, so downstream duration budgeting (cue_available_durations) and
    edl.py's caption-snapping see distinct, ordered anchor points."""
    by_line: dict[int, list[int]] = {}
    for i, c in enumerate(cues):
        if c.anchor_line_index is not None and c.anchor_src_t is not None:
            by_line.setdefault(c.anchor_line_index, []).append(i)
    for line_idx, idxs in by_line.items():
        if len(idxs) < 2:
            continue
        times = [cues[i].anchor_src_t for i in idxs]
        if len(set(round(t, 2) for t in times)) == len(times):
            continue  # already distinct -- real, independently-resolved anchors
        window = line_window(line_idx)
        if window is None:
            continue
        t0, t1 = window
        movable = [i for i in idxs if cues[i].confidence < 1.0]  # 1.0 == manual pin, never move
        if len(movable) < 2:
            continue
        idxs_sorted = sorted(movable, key=lambda i: (cues[i].anchor_src_t, i))
        n = len(idxs_sorted)
        for rank, i in enumerate(idxs_sorted):
            frac = (rank + 0.5) / n
            cues[i].anchor_src_t = round(t0 + frac * (t1 - t0), 3)


def _first_quoted_word(text: str) -> Optional[str]:
    m = _QUOTED_RE.search(text)
    if not m:
        return None
    phrase = m.group(1) or m.group(2) or ""
    words = [w for w in phrase.split() if w]
    return words[-1] if words else None  # last word of the quote is usually the punchline


def _find_word_in_window(sentences: list[dict], words: list[dict], t0: float,
                         t1: float, target_word: str) -> Optional[float]:
    """Exact match using the sentence's real word_indices → words.json timestamps."""
    target = norm_token(target_word)
    if not target:
        return None
    for s in sentences:
        if s["t1"] < t0 - 0.05 or s["t0"] > t1 + 0.05:
            continue
        for i in s["word_indices"]:
            if norm_token(words[i]["w"]) == target:
                return words[i]["t0"]
    return None
