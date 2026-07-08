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
from . import audio_analysis, brain, edl as edl_mod, ingest, qc, render, segments, transcribe
from .ffmpeg import ProcHolder

ProgressFn = Callable[[str, float, str], None]  # (stage, stage_progress 0..1, message)

QC_MAX_RETRIES = 2
QC_WIDEN_S = 0.08


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


def run_render_variant(*, video_id: str, render_id: str, variant: str,
                       analysis: dict, brain_result: BrainResult,
                       settings: RenderSettings, source_duration: float,
                       holder: Optional[ProcHolder] = None,
                       progress: ProgressFn = _noop) -> dict:
    """Render one variant with the QC retry loop. Returns render summary."""
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
