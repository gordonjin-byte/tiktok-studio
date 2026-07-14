"""Stage orchestration — used by both the worker and the headless CLI.

Caching contract:
- analysis artifacts (words/silence/energy/segments) cached per video+ANALYSIS_VERSION
- brain.json cached per video (explicit regenerate only)
- edl/render recomputed per render request (fast), idempotent via settings_hash
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Optional

from .. import config
from ..models import BrainResult, RenderSettings
from . import (audio_analysis, bespoke_codegen, brain, edl as edl_mod, ingest,
              overlay_advisor, overlay_catalog, overlay_qc, overlays, qc, render,
              script_align, segments, transcribe)
from .ffmpeg import ProcHolder
from .script_parse import ScriptDoc

ProgressFn = Callable[[str, float, str], None]  # (stage, stage_progress 0..1, message)

QC_MAX_RETRIES = 2
QC_WIDEN_S = 0.08
# each escalation costs a full bespoke-codegen cycle + a re-render + a second
# vision call — real time and money, stay conservative
VISUAL_QC_MAX_ESCALATIONS = 1


def _noop(stage: str, p: float, msg: str = "") -> None:
    pass


def analysis_is_cached(video_id: str) -> bool:
    art = config.ARTIFACTS_DIR / video_id
    manifest = art / "manifest.json"
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text())
    except json.JSONDecodeError:
        return False
    if data.get("analysis_version") != config.ANALYSIS_VERSION:
        return False
    return all((art / f).exists() for f in
               ("audio.wav", "words.json", "silence.json", "energy.json", "speech_segments.json"))


def run_analysis(video_id: str, *, holder: Optional[ProcHolder] = None,
                 progress: ProgressFn = _noop, force: bool = False) -> dict:
    art = config.ARTIFACTS_DIR / video_id
    art.mkdir(exist_ok=True)
    if not force and analysis_is_cached(video_id):
        progress("analyze", 1.0, "analysis cache hit — skipping")
        return _load_analysis(video_id)

    progress("transcribe", 0.0, "extracting audio")
    wav = ingest.extract_wav(video_id, holder)
    progress("analyze", 0.05, "silence + energy analysis")
    energy = audio_analysis.analyze(video_id, wav, holder)
    # transcribe silence-stripped audio for accurate word times, map back to source
    progress("transcribe", 0.1, "building dense audio")
    regions = audio_analysis.fixed_speech_regions(energy["db"])
    dense_wav = art / "dense.wav"
    dense_map = audio_analysis.build_dense_wav(wav, regions, dense_wav, holder)
    progress("transcribe", 0.2, "transcribing (whisper small.en, dense)")
    words = transcribe.transcribe_words(dense_wav, art / "words.json", holder)
    for w in words:
        t0, t1 = audio_analysis.map_word_span(w["t0"], w["t1"], dense_map)
        w["t0"] = round(t0, 3)
        w["t1"] = round(max(t1, t0 + 0.05), 3)
    (art / "words.json").write_text(json.dumps({"words": words}, indent=1))
    progress("analyze", 0.7, "building segments + retake candidates")
    segments.build_segments(video_id, words)
    (art / "manifest.json").write_text(json.dumps({
        "analysis_version": config.ANALYSIS_VERSION,
    }))
    progress("analyze", 1.0, "analysis complete")
    return _load_analysis(video_id)


def _load_analysis(video_id: str) -> dict:
    art = config.ARTIFACTS_DIR / video_id
    return {
        "words": json.loads((art / "words.json").read_text())["words"],
        "energy": json.loads((art / "energy.json").read_text()),
        "segments": json.loads((art / "speech_segments.json").read_text()),
    }


def load_cached_analysis(video_id: str) -> dict:
    """script_plan reads already-cached analysis rather than re-running it —
    the API layer requires the video be analyzed before a script is attached."""
    return _load_analysis(video_id)


def load_cached_brain(video_id: str) -> tuple[BrainResult, str]:
    analysis = _load_analysis(video_id)
    cached = brain.load_brain(video_id, analysis["words"], analysis["segments"])
    if not cached:
        raise RuntimeError(f"no cached brain result for video {video_id}")
    return cached


def run_brain_stage(video_id: str, analysis: dict, *, filename_hint: str = "",
                    use_claude: bool = True, force: bool = False,
                    progress: ProgressFn = _noop) -> tuple[BrainResult, str]:
    if not force:
        cached = brain.load_brain(video_id, analysis["words"], analysis["segments"])
        if cached:
            progress("brain", 1.0, f"brain cache hit ({cached[1]})")
            return cached
    progress("brain", 0.1, "asking Claude for edit decisions" if use_claude else "heuristic edit decisions")
    result = brain.run_brain(video_id, analysis["words"], analysis["segments"],
                             filename_hint=filename_hint, use_claude=use_claude)
    progress("brain", 1.0, f"brain done ({result[1]})")
    return result


def run_script_plan(video_id: str, script_id: str, analysis: dict,
                    brain_result: BrainResult, progress: ProgressFn = _noop) -> None:
    """script_plan job body: align -> advise -> bespoke codegen. Never raises
    for per-cue failures — those degrade to the universal fallback template;
    only a missing video/script row or a truly corrupt parse is fatal."""
    from .. import db, events

    video_row = db.query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    script_row = db.query_one("SELECT * FROM scripts WHERE id=?", (script_id,))
    if not video_row or not script_row:
        raise RuntimeError("video or script row missing")
    script_doc = ScriptDoc.model_validate(json.loads(script_row["parsed_json"]))
    lines_by_index = {l.index: l for l in script_doc.lines}
    cues_by_index = {c.index: c for c in script_doc.cues}

    def publish_cue(cue_id: str, status: str) -> None:
        events.publish("cue_update", {"video_id": video_id, "script_id": script_id,
                                      "cue_id": cue_id, "decision_status": status})

    # ---- 1. align script lines/cues to the real transcript ----
    db.update("scripts", script_id, {"status": "aligning", "updated_at": db.now()})
    progress("align", 0.0, "aligning script to real transcript")
    alignment = script_align.align_script(analysis["words"], analysis["segments"], brain_result, script_doc)
    aligned_by_index = {c.index: c for c in alignment.cues}
    aligned_lines_by_index = {l.index: l for l in alignment.lines}
    available_by_index = script_align.cue_available_durations(alignment)

    def _line_window_for_cue(cue_index: int) -> tuple[Optional[float], Optional[float]]:
        a = aligned_by_index.get(cue_index)
        line = aligned_lines_by_index.get(a.anchor_line_index) if (a and a.anchor_line_index is not None) else None
        return (line.t0, line.t1) if (line and line.matched) else (None, None)

    cue_rows = db.query("SELECT * FROM script_cues WHERE script_id=? ORDER BY cue_index", (script_id,))
    est_overlay_cues = [
        {
            "cue_id": row["id"], "kind": row["cue_type"],
            "anchor_src_t": aligned_by_index[row["cue_index"]].anchor_src_t,
            "duration_s": available_by_index.get(row["cue_index"], script_align.DEFAULT_AVAILABLE_DURATION_S),
            "spec": {},
            "line_src_t0": _line_window_for_cue(row["cue_index"])[0],
            "line_src_t1": _line_window_for_cue(row["cue_index"])[1],
        }
        for row in cue_rows if row["cue_index"] in aligned_by_index
    ]
    # real-duration output-time estimate for the UI/advisor context — uses the
    # SAME available_duration_s computed from genuine aligned-line data that the
    # advisor sees below, and the SAME caption-snapping logic build_edl applies
    # for the real render, so this estimate no longer diverges from what
    # actually gets rendered
    est_edl = edl_mod.build_edl(
        words=analysis["words"], energy=analysis["energy"], brain=brain_result,
        settings=RenderSettings(), source_duration=video_row["duration_s"] or 0.0,
        variant="hook_a", overlay_cues=est_overlay_cues)
    est_by_cue_id = {e["cue_id"]: e for e in est_edl["overlay_events"]}

    for row in cue_rows:
        a = aligned_by_index.get(row["cue_index"])
        est = est_by_cue_id.get(row["id"])
        line_t0, line_t1 = _line_window_for_cue(row["cue_index"])
        db.update("script_cues", row["id"], {
            "anchor_src_t": a.anchor_src_t if a else None,
            "match_confidence": a.confidence if a else 0.0,
            "line_src_t0": line_t0,
            "line_src_t1": line_t1,
            "resolved_out_t0_s": est["start_out"] if est else None,
            "resolved_out_t1_s": est["end_out"] if est else None,
            "updated_at": db.now(),
        })
    progress("align", 1.0, "aligned")

    # ---- 2. advisor: template-vs-bespoke decision per cue ----
    db.update("scripts", script_id, {"status": "planning", "updated_at": db.now()})
    progress("advise", 0.0, "planning overlays")
    episode_meta = {
        "title": script_row["episode_title"] or "", "category": script_row["episode_category"] or "",
        "difficulty": script_row["episode_difficulty"] or "", "builds": script_row["builds_text"] or "",
        "new_piece": script_row["new_piece_text"] or "",
    }
    cue_rows = db.query("SELECT * FROM script_cues WHERE script_id=? ORDER BY cue_index", (script_id,))
    advisor_inputs: list[overlay_advisor.CueInput] = []
    checksum_by_cue: dict[str, str] = {}
    for row in cue_rows:
        cue = cues_by_index.get(row["cue_index"])
        nearby = lines_by_index[cue.anchor_line_index].text if cue and cue.anchor_line_index in lines_by_index else ""
        checksum = overlay_advisor.cue_advisor_checksum(row["source_text"], nearby, episode_meta)
        checksum_by_cue[row["id"]] = checksum
        if row["advisor_checksum"] == checksum and (
                row["user_overridden"] or row["decision_status"] in ("decided", "bespoke_ready", "bespoke_failed")):
            continue  # cached decision still valid — skip re-asking the advisor
        available = available_by_index.get(row["cue_index"], script_align.DEFAULT_AVAILABLE_DURATION_S)
        advisor_inputs.append(overlay_advisor.CueInput(
            cue_id=row["id"], cue_type=row["cue_type"], source_text=row["source_text"],
            nearby_dialogue=nearby, available_duration_s=available))

    decisions, advisor_status = overlay_advisor.plan_cues(advisor_inputs, episode_meta, use_claude=True)
    by_cue_id = {d.cue_id: d for d in decisions}
    for row in cue_rows:
        d = by_cue_id.get(row["id"])
        if d is None:
            continue
        status = "decided" if d.kind == "template" else "bespoke_pending"
        db.update("script_cues", row["id"], {
            "decision_kind": d.kind, "template_id": d.template_id,
            "template_props_json": json.dumps(d.props),
            "bespoke_brief": d.bespoke_brief, "duration_s": d.duration_s,
            "advisor_checksum": checksum_by_cue[row["id"]], "advisor_status": d.advisor_status,
            "decision_reason": d.reason, "advisor_confidence": d.advisor_confidence,
            "decision_status": status, "updated_at": db.now(),
        })
        publish_cue(row["id"], status)
    progress("advise", 1.0, f"overlay plan ready ({advisor_status})")

    # ---- 3. bespoke codegen for flagged cues ----
    bespoke_rows = db.query(
        "SELECT * FROM script_cues WHERE script_id=? AND decision_status='bespoke_pending'", (script_id,))
    progress("codegen", 0.0, f"generating {len(bespoke_rows)} bespoke overlays" if bespoke_rows else "no bespoke overlays needed")
    for i, row in enumerate(bespoke_rows):
        ok, module_path, error = bespoke_codegen.generate(video_id, row["id"], row["bespoke_brief"] or "", episode_meta)
        if ok:
            db.update("script_cues", row["id"], {
                "decision_status": "bespoke_ready", "bespoke_module_path": module_path, "updated_at": db.now()})
            publish_cue(row["id"], "bespoke_ready")
        else:
            # degrade to the universal fallback template — never block the pipeline
            db.update("script_cues", row["id"], {
                "decision_status": "bespoke_failed", "bespoke_error": error[:2000],
                "decision_kind": "template", "template_id": overlay_catalog.fallback_template_id(),
                "template_props_json": json.dumps({"text": row["source_text"][:200]}),
                "updated_at": db.now(),
            })
            publish_cue(row["id"], "bespoke_failed")
        progress("codegen", (i + 1) / max(1, len(bespoke_rows)), f"{i + 1}/{len(bespoke_rows)}")

    db.update("scripts", script_id, {"status": "planned", "updated_at": db.now()})


def _expected_description(row: dict) -> str:
    # bespoke_brief is more concrete/visual than source_text when present
    # (overlay_advisor's bespoke path already asked Claude to write a
    # concrete visual brief) — prefer it, fall back to the raw authored cue text.
    return (row.get("bespoke_brief") or "").strip() or row["source_text"]


def run_overlay_qc_stage(video_id: str, script_id: str, episode_meta: dict,
                         holder: Optional[ProcHolder] = None,
                         progress: ProgressFn = _noop) -> dict:
    """Visually QCs every renderable cue, skipping ones whose manifest
    spec_hash is unchanged since a prior terminal (pass/failed) QC result.
    Never raises for a per-cue failure — the video always finishes rendering."""
    from .. import db, events

    rows = db.query(
        "SELECT * FROM script_cues WHERE script_id=? AND decision_status IN "
        "('decided','bespoke_ready','bespoke_failed') ORDER BY cue_index", (script_id,))
    manifest = overlays.get_manifest(video_id)
    n_checked = n_skipped = n_escalated = n_failed = 0

    for i, row in enumerate(rows):
        progress("visual_qc", i / max(1, len(rows)), f"checking overlay {i + 1}/{len(rows)}")
        try:
            entry = manifest.get(row["id"], {})
            clip_path = config.ARTIFACTS_DIR / video_id / "overlays" / f"{row['id']}.mov"
            if "error" in entry or not clip_path.exists():
                continue  # no clip to check — a Remotion-level render failure, a separate axis
            spec_hash = entry.get("spec_hash")
            if spec_hash and row.get("visual_qc_spec_hash") == spec_hash \
                    and row.get("visual_qc_status") in ("pass", "failed"):
                n_skipped += 1
                continue

            report = overlay_qc.run_overlay_visual_qc(
                video_id=video_id, cue_id=row["id"], expected_description=_expected_description(row),
                duration_s=row.get("duration_s") or 2.0, holder=holder)
            n_checked += 1
            result = _apply_qc_result(video_id, script_id, row, report, spec_hash, episode_meta,
                                      attempt=0, holder=holder)
            if result.get("visual_qc_status") == "failed":
                n_failed += 1
            if result.get("_escalated"):
                n_escalated += 1
            events.publish("cue_update", {
                "video_id": video_id, "script_id": script_id, "cue_id": row["id"],
                "decision_status": result.get("decision_status"),
                "visual_qc_status": result.get("visual_qc_status")})
        except Exception:
            continue  # never let one cue's QC blow up the whole stage
    progress("visual_qc", 1.0, f"visual QC: {n_checked} checked, {n_skipped} cached, "
                              f"{n_escalated} escalated, {n_failed} failed")
    return {"checked": n_checked, "skipped": n_skipped, "escalated": n_escalated, "failed": n_failed}


def _apply_qc_result(video_id: str, script_id: str, row: dict, report: dict,
                     spec_hash: Optional[str], episode_meta: dict,
                     attempt: int, holder: Optional[ProcHolder]) -> dict:
    from .. import db

    if not report["checked"]:
        db.update("script_cues", row["id"], {
            "visual_qc_status": "skipped", "visual_qc_report": json.dumps(report),
            "updated_at": db.now()})
        return {**row, "visual_qc_status": "skipped"}

    if report["verdict"] == "pass":
        db.update("script_cues", row["id"], {
            "visual_qc_status": "pass", "visual_qc_report": json.dumps(report),
            "visual_qc_spec_hash": spec_hash, "updated_at": db.now()})
        return {**row, "visual_qc_status": "pass"}

    def _degrade_to_fallback(reason_report: dict) -> None:
        db.update("script_cues", row["id"], {
            "decision_kind": "template", "template_id": overlay_catalog.fallback_template_id(),
            "template_props_json": json.dumps({"text": row["source_text"][:200]}),
            "visual_qc_status": "failed", "visual_qc_report": json.dumps(reason_report),
            "updated_at": db.now()})
        _rerender_one(video_id, row["id"], holder=holder)
        # the fallback clip has a DIFFERENT spec_hash than whatever was being
        # checked when this failure was decided — capture it now so the next
        # render's cache check (spec_hash == visual_qc_spec_hash) actually
        # matches and this terminal "failed" cue is never re-billed for
        # another vision check just because it degraded.
        fresh_hash = overlays.get_manifest(video_id).get(row["id"], {}).get("spec_hash")
        if fresh_hash:
            db.update("script_cues", row["id"], {"visual_qc_spec_hash": fresh_hash, "updated_at": db.now()})

    if attempt >= VISUAL_QC_MAX_ESCALATIONS:
        _degrade_to_fallback(report)
        return {**row, "visual_qc_status": "failed", "decision_kind": "template"}

    # a template render is deterministic (same template+props always renders
    # identically) — retrying the SAME template is pointless, escalate to
    # bespoke, seeded with the QC's own corrective feedback
    if row["decision_kind"] == "template":
        brief = (f"{row['source_text']}\n\n"
                f"(A previous automatic attempt used a generic template that "
                f"didn't match: {report['problem']} {report['suggestion']})")
    else:
        brief = (row.get("bespoke_brief") or row["source_text"]) + (
            f"\n\n(A previous generated version had this problem: "
            f"{report['problem']} Fix: {report['suggestion']})")

    ok, module_path, error = bespoke_codegen.generate(video_id, row["id"], brief, episode_meta)
    if not ok:
        db.update("script_cues", row["id"], {"bespoke_error": error[:2000],
                  "decision_status": "bespoke_failed", "updated_at": db.now()})
        _degrade_to_fallback(report)
        return {**row, "visual_qc_status": "failed", "decision_kind": "template"}

    db.update("script_cues", row["id"], {
        "decision_kind": "bespoke", "template_id": None, "template_props_json": "{}",
        "bespoke_brief": brief, "bespoke_module_path": module_path, "bespoke_error": None,
        "decision_status": "bespoke_ready", "updated_at": db.now()})
    _rerender_one(video_id, row["id"], holder=holder)

    fresh_row = db.query_one("SELECT * FROM script_cues WHERE id=?", (row["id"],))
    fresh_entry = overlays.get_manifest(video_id).get(row["id"], {})
    if "error" in fresh_entry:
        db.update("script_cues", row["id"], {
            "visual_qc_status": "skipped", "visual_qc_report": json.dumps(report),
            "updated_at": db.now()})
        return {**fresh_row, "visual_qc_status": "skipped", "_escalated": True}

    new_report = overlay_qc.run_overlay_visual_qc(
        video_id=video_id, cue_id=row["id"], expected_description=_expected_description(fresh_row),
        duration_s=fresh_row.get("duration_s") or 2.0, holder=holder)
    result = _apply_qc_result(video_id, script_id, fresh_row, new_report,
                              fresh_entry.get("spec_hash"), episode_meta,
                              attempt=attempt + 1, holder=holder)
    result["_escalated"] = True
    return result


def _rerender_one(video_id: str, cue_id: str, holder: Optional[ProcHolder] = None) -> None:
    from .. import db
    row = db.query_one("SELECT * FROM script_cues WHERE id=?", (cue_id,))
    specs = overlay_advisor.to_cue_render_specs([row])
    if specs:
        overlays.render_overlay_batch(video_id, specs, holder=holder)


def run_render_variant(*, video_id: str, render_id: str, variant: str,
                       analysis: dict, brain_result: BrainResult,
                       settings: RenderSettings, source_duration: float,
                       holder: Optional[ProcHolder] = None,
                       progress: ProgressFn = _noop,
                       overlay_clips: Optional[list[dict]] = None) -> dict:
    """Render one variant with the QC retry loop. Returns render summary.

    overlay_clips: [{"cue_id","anchor_src_t","duration_s","spec"}] — rendered
    Remotion clips for this video (shared across all 3 hook variants; only
    their source->output placement differs per variant's own EDL)."""
    render_dir = config.RENDERS_DIR / video_id / render_id
    boundary_padding: dict[int, float] = {}
    qc_report: dict = {}
    out_path: Optional[Path] = None

    for attempt in range(QC_MAX_RETRIES + 1):
        the_edl = edl_mod.build_edl(
            words=analysis["words"], energy=analysis["energy"],
            brain=brain_result, settings=settings,
            source_duration=source_duration, variant=variant,
            boundary_padding=boundary_padding or None,
            overlay_cues=overlay_clips,
        )
        label = f"render:{variant}" + (f" (retry {attempt})" if attempt else "")
        progress(label, 0.0, "rendering")
        out_path = render.render_variant(
            edl=the_edl, settings=settings, video_id=video_id,
            render_dir=render_dir, holder=holder,
            progress_cb=lambda p: progress(label, p, "rendering"),
        )
        progress("qc", 0.2, "verifying no words were clipped")
        qc_report = qc.run_qc(rendered=out_path, edl=the_edl,
                              work_dir=render_dir, holder=holder)
        (render_dir / "qc.json").write_text(json.dumps(qc_report, indent=1))
        if qc_report["pass"] or not qc_report["widen_intervals"]:
            break
        for idx in qc_report["widen_intervals"]:
            boundary_padding[idx] = boundary_padding.get(idx, 0.0) + QC_WIDEN_S
        progress("qc", 0.5, f"clipped words near splice — widening {qc_report['widen_intervals']} and retrying")

    progress("qc", 0.8, "extracting stills")
    render.extract_stills(out_path, render_dir / "thumbs",
                          duration_s=the_edl["total_out_s"], holder=holder)
    render.extract_poster(out_path, render_dir / "poster.jpg", holder=holder)
    from .ffmpeg import ffprobe_info
    info = ffprobe_info(out_path)
    return {
        "output_path": str(out_path),
        "duration_s": info["duration_s"],
        "size_bytes": info["size_bytes"],
        "qc": qc_report,
        "status": "done" if qc_report.get("pass") else "done_with_warnings",
    }


# ---------- headless CLI ----------

def main() -> None:
    import argparse
    import time

    from .. import db

    parser = argparse.ArgumentParser(description="Headless pipeline run")
    parser.add_argument("video", type=Path)
    parser.add_argument("--variants", default="hook_a,hook_b,hook_c")
    parser.add_argument("--no-brain", action="store_true", help="skip claude, use heuristics")
    parser.add_argument("--settings", type=Path, help="JSON settings file")
    parser.add_argument("--force-analyze", action="store_true")
    parser.add_argument("--force-brain", action="store_true")
    args = parser.parse_args()

    config.ensure_dirs()
    db.get_conn()

    def progress(stage: str, p: float, msg: str = "") -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {stage:<22} {p * 100:5.1f}%  {msg}")

    settings = RenderSettings()
    if args.settings:
        settings = RenderSettings.model_validate(json.loads(args.settings.read_text()))

    src = args.video.expanduser()
    sha = ingest.sha256_file(src)
    existing = db.query_one("SELECT * FROM videos WHERE sha256=?", (sha,))
    if existing:
        video_id = existing["id"]
        info = {k: existing[k] for k in ("duration_s", "width", "height", "fps", "size_bytes")}
        print(f"dedupe: already ingested as {video_id}")
    else:
        video_id = db.new_id()
        work = config.TMP_DIR / f"cli_{video_id}{src.suffix.lower()}"
        shutil.copy2(src, work)
        info = ingest.ingest_file(work, video_id)
        db.insert("videos", {
            "id": video_id, "filename": src.name, "sha256": sha,
            "duration_s": info["duration_s"], "width": info["width"], "height": info["height"],
            "fps": info["fps"], "size_bytes": info["size_bytes"],
            "status": "ingested", "created_at": db.now(),
        })
        print(f"ingested as {video_id}")

    analysis = run_analysis(video_id, progress=progress, force=args.force_analyze)
    db.update("videos", video_id, {"status": "analyzed",
                                   "analysis_version": config.ANALYSIS_VERSION})
    brain_result, brain_status = run_brain_stage(
        video_id, analysis, filename_hint=src.name,
        use_claude=not args.no_brain, force=args.force_brain, progress=progress)
    db.update("videos", video_id, {"brain_status": brain_status})

    for variant in args.variants.split(","):
        render_id = db.new_id()
        summary = run_render_variant(
            video_id=video_id, render_id=render_id, variant=variant.strip(),
            analysis=analysis, brain_result=brain_result, settings=settings,
            source_duration=info["duration_s"], progress=progress)
        db.insert("renders", {
            "id": render_id, "video_id": video_id, "job_id": None,
            "variant": variant.strip(),
            "settings_json": settings.canonical_json(),
            "settings_hash": settings.settings_hash(config.ANALYSIS_VERSION),
            "status": summary["status"], "qc_json": json.dumps(summary["qc"]),
            "output_path": summary["output_path"],
            "duration_s": summary["duration_s"], "size_bytes": summary["size_bytes"],
            "created_at": db.now(),
        })
        print(f"  → {variant}: {summary['output_path']} "
              f"({summary['duration_s']:.1f}s, qc={summary['qc']['match_ratio']}, {summary['status']})")


if __name__ == "__main__":
    main()
