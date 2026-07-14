"""Job worker: durable queue in sqlite, one job at a time (ffmpeg/whisper
saturate the machine). Pipeline stages run in a thread; cancellation kills the
registered subprocess group. Startup recovery marks interrupted jobs failed —
the analysis cache makes re-running them cheap."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import traceback
from typing import Optional

from . import config, db, events
from .models import RenderSettings
from .pipeline import overlay_advisor, overlays
from .pipeline import run as pipeline
from .pipeline.ffmpeg import PipelineCancelled, ProcHolder

_wakeup: Optional[asyncio.Event] = None
_holders: dict[str, ProcHolder] = {}  # job_id → holder (for cancel)

# stage weights for overall progress ("full"/"render"/"analyze" jobs)
_WEIGHTS = [("ingest", 4), ("transcribe", 15), ("analyze", 5), ("brain", 6),
            ("render_overlays", 10),
            ("render:hook_a", 16), ("render:hook_b", 16), ("render:hook_c", 16),
            ("qc", 8), ("package", 2)]
# stage weights for "script_plan" jobs (parse already ran in the API layer)
_SCRIPT_PLAN_WEIGHTS = [("align", 15), ("advise", 45), ("codegen", 40)]


def enqueue(video_id: str, job_type: str, settings: RenderSettings,
            variants: list[str], script_id: Optional[str] = None) -> str:
    free = shutil.disk_usage(config.DATA_DIR).free
    if free < config.MIN_FREE_DISK_BYTES:
        raise RuntimeError(f"low disk space ({free / 1e9:.1f}GB free) — refusing new job")
    job_id = db.new_id()
    db.insert("jobs", {
        "id": job_id, "video_id": video_id, "type": job_type,
        "settings_json": settings.canonical_json(),
        "variants_json": json.dumps(variants),
        "status": "queued", "created_at": db.now(), "script_id": script_id,
    })
    events.publish("job_update", {"job_id": job_id, "video_id": video_id,
                                  "status": "queued", "stage": "", "progress": 0})
    if _wakeup:
        _wakeup.set()
    return job_id


def cancel(job_id: str) -> bool:
    job = db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job or job["status"] not in ("queued", "running"):
        return False
    holder = _holders.get(job_id)
    if holder:
        holder.cancel()
    db.update("jobs", job_id, {"status": "canceled", "finished_at": db.now()})
    events.publish("job_update", {"job_id": job_id, "video_id": job["video_id"],
                                  "status": "canceled", "stage": job["stage"],
                                  "progress": job["progress"]})
    return True


def recover_on_startup() -> None:
    for job in db.query("SELECT id, video_id FROM jobs WHERE status='running'"):
        db.update("jobs", job["id"], {
            "status": "failed", "error": "interrupted by server restart",
            "finished_at": db.now()})
    db.execute("UPDATE videos SET status='analyzed' WHERE status='analyzing' AND id IN "
               "(SELECT id FROM videos WHERE analysis_version IS NOT NULL)")
    db.execute("UPDATE videos SET status='error' WHERE status='analyzing'")


async def worker_loop() -> None:
    global _wakeup
    _wakeup = asyncio.Event()
    while True:
        job = db.query_one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
        if not job:
            _wakeup.clear()
            try:
                await asyncio.wait_for(_wakeup.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            continue
        holder = ProcHolder()
        _holders[job["id"]] = holder
        try:
            await asyncio.to_thread(_run_job, job, holder)
        except Exception:
            traceback.print_exc()
        finally:
            _holders.pop(job["id"], None)


def shutdown() -> None:
    for holder in _holders.values():
        holder.cancel()


def _run_job(job: dict, holder: ProcHolder) -> None:
    job_id, video_id = job["id"], job["video_id"]
    current = db.query_one("SELECT status FROM jobs WHERE id=?", (job_id,))
    if current["status"] != "queued":
        return
    db.update("jobs", job_id, {"status": "running", "started_at": db.now()})
    settings = RenderSettings.model_validate(json.loads(job["settings_json"]))
    variants = json.loads(job["variants_json"])
    weights = _SCRIPT_PLAN_WEIGHTS if job["type"] == "script_plan" else _WEIGHTS

    stage_state = {"stage": "", "message": ""}

    def progress(stage: str, p: float, msg: str = "") -> None:
        base_stage = stage.split(" ")[0]
        done = 0.0
        total = sum(w for _, w in weights)
        for name, w in weights:
            if name == base_stage:
                done += w * min(max(p, 0.0), 1.0)
                break
            done += w
        overall = min(done / total, 1.0)
        stage_state["stage"], stage_state["message"] = stage, msg
        db.update("jobs", job_id, {"stage": stage, "progress": round(overall, 4),
                                   "message": msg})
        events.publish("job_update", {
            "job_id": job_id, "video_id": video_id, "status": "running",
            "stage": stage, "progress": round(overall, 4), "message": msg})

    try:
        video = db.query_one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video:
            raise RuntimeError("video row missing")

        if job["type"] == "script_plan":
            analysis = pipeline.load_cached_analysis(video_id)
            brain_result, _ = pipeline.load_cached_brain(video_id)
            pipeline.run_script_plan(video_id, job["script_id"], analysis, brain_result, progress=progress)
            db.update("jobs", job_id, {"status": "done", "progress": 1.0, "finished_at": db.now()})
            events.publish("job_update", {"job_id": job_id, "video_id": video_id,
                                          "status": "done", "progress": 1.0})
            return

        force_analyze = job["type"] == "analyze"
        needs_analysis = force_analyze or not pipeline.analysis_is_cached(video_id)
        if needs_analysis:
            db.update("videos", video_id, {"status": "analyzing"})
            events.publish("video_update", {"video_id": video_id, "status": "analyzing"})
        analysis = pipeline.run_analysis(video_id, holder=holder,
                                         progress=progress, force=force_analyze)
        db.update("videos", video_id, {"status": "analyzed",
                                       "analysis_version": config.ANALYSIS_VERSION})
        events.publish("video_update", {"video_id": video_id, "status": "analyzed"})

        brain_result, brain_status = pipeline.run_brain_stage(
            video_id, analysis, filename_hint=video["filename"],
            use_claude=True, force=force_analyze, progress=progress)
        db.update("videos", video_id, {"brain_status": brain_status})
        events.publish("video_update", {"video_id": video_id, "brain_status": brain_status})

        if job["type"] == "analyze":
            db.update("jobs", job_id, {"status": "done", "progress": 1.0,
                                       "finished_at": db.now()})
            events.publish("job_update", {"job_id": job_id, "video_id": video_id,
                                          "status": "done", "stage": "package", "progress": 1.0})
            return

        overlay_clips: list[dict] = []
        script_id = job.get("script_id")
        if script_id:
            script_cue_rows = db.query(
                "SELECT * FROM script_cues WHERE script_id=? ORDER BY cue_index", (script_id,))
            cue_specs = overlay_advisor.to_cue_render_specs(script_cue_rows)
            progress("render_overlays", 0.0, f"rendering {len(cue_specs)} overlay clip(s)")
            overlay_result = overlays.render_overlay_batch(video_id, cue_specs, holder=holder)
            n_ready = len(overlay_result["rendered"]) + len(overlay_result["skipped"])
            progress("render_overlays", 1.0, f"{n_ready}/{len(cue_specs)} overlay(s) ready")
            overlay_clips = [
                {"cue_id": r["id"], "kind": r["cue_type"], "anchor_src_t": r["anchor_src_t"],
                 "duration_s": r["duration_s"], "spec": {}}
                for r in script_cue_rows
                if r["anchor_src_t"] is not None
                and r["decision_status"] in ("decided", "bespoke_ready", "bespoke_failed")
            ]
            script_fingerprint = hashlib.sha256(json.dumps(
                [(r["id"], r["decision_kind"], r["template_id"], r["template_props_json"],
                  r["bespoke_module_path"], r["updated_at"]) for r in script_cue_rows],
                sort_keys=True).encode()).hexdigest()[:16]
        else:
            progress("render_overlays", 1.0, "no script attached — skipping")
            script_fingerprint = ""

        settings_hash = settings.settings_hash(config.ANALYSIS_VERSION)
        for variant in variants:
            holder.check()
            existing = db.query_one(
                "SELECT id FROM renders WHERE video_id=? AND variant=? AND settings_hash=? "
                "AND script_fingerprint=? AND status IN ('done','done_with_warnings')",
                (video_id, variant, settings_hash, script_fingerprint))
            if existing:
                progress(f"render:{variant}", 1.0, "identical render exists — skipping")
                continue
            render_id = db.new_id()
            db.insert("renders", {
                "id": render_id, "video_id": video_id, "job_id": job_id,
                "variant": variant, "settings_json": settings.canonical_json(),
                "settings_hash": settings_hash, "script_fingerprint": script_fingerprint,
                "status": "rendering", "created_at": db.now(),
            })
            summary = pipeline.run_render_variant(
                video_id=video_id, render_id=render_id, variant=variant,
                analysis=analysis, brain_result=brain_result, settings=settings,
                source_duration=video["duration_s"], holder=holder, progress=progress,
                overlay_clips=overlay_clips)
            db.update("renders", render_id, {
                "status": summary["status"], "qc_json": json.dumps(summary["qc"]),
                "output_path": summary["output_path"],
                "duration_s": summary["duration_s"], "size_bytes": summary["size_bytes"]})
            if script_id:
                db.insert("script_renders", {
                    "id": db.new_id(), "script_id": script_id, "render_id": render_id,
                    "job_id": job_id, "created_at": db.now(),
                })
            events.publish("render_done", {
                "video_id": video_id, "render_id": render_id, "variant": variant,
                "status": summary["status"]})

        db.set_state("last_settings", json.loads(settings.canonical_json()))
        db.update("jobs", job_id, {"status": "done", "progress": 1.0,
                                   "stage": "package", "finished_at": db.now()})
        events.publish("job_update", {"job_id": job_id, "video_id": video_id,
                                      "status": "done", "stage": "package", "progress": 1.0})
    except PipelineCancelled:
        db.execute("UPDATE renders SET status='failed' WHERE job_id=? AND status='rendering'",
                   (job_id,))
    except Exception as e:
        db.execute("UPDATE renders SET status='failed' WHERE job_id=? AND status='rendering'",
                   (job_id,))
        db.update("jobs", job_id, {"status": "failed", "error": str(e)[-1800:],
                                   "finished_at": db.now()})
        events.publish("job_update", {"job_id": job_id, "video_id": video_id,
                                      "status": "failed", "stage": stage_state["stage"],
                                      "progress": 0, "message": str(e)[-300:]})
