from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config, db, watcher, worker
from ..models import RenderSettings, VARIANTS
from ..pipeline.ingest import original_path_for

router = APIRouter()


@router.post("/api/videos")
async def upload(request: Request):
    filename = request.headers.get("x-filename", "upload.mp4")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".mp4"
    if suffix not in config.VIDEO_EXTENSIONS:
        raise HTTPException(400, f"unsupported file type {suffix}")
    tmp = config.TMP_DIR / f"upload_{db.new_id()}{suffix}"
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        if tmp.stat().st_size < 1000:
            raise HTTPException(400, "empty upload")
        result = await __import__("asyncio").to_thread(
            watcher.ingest_and_enqueue, tmp, filename)
    finally:
        tmp.unlink(missing_ok=True)
    return result


@router.get("/api/videos")
def list_videos(limit: int = 100, offset: int = 0, q: str = ""):
    where, args = "", []
    if q:
        where = "WHERE filename LIKE ?"
        args.append(f"%{q}%")
    videos = db.query(
        f"SELECT * FROM videos {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (*args, limit, offset))
    for v in videos:
        v["renders"] = db.query(
            "SELECT id, variant, status, duration_s, size_bytes, created_at "
            "FROM renders WHERE video_id=? AND status LIKE 'done%' "
            "ORDER BY created_at DESC LIMIT 6", (v["id"],))
        job = db.query_one(
            "SELECT id, status, stage, progress, message FROM jobs "
            "WHERE video_id=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT 1", (v["id"],))
        v["active_job"] = job
    return {"videos": videos}


@router.get("/api/videos/{video_id}")
def get_video(video_id: str):
    video = db.query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not video:
        raise HTTPException(404)
    video["renders"] = db.query(
        "SELECT * FROM renders WHERE video_id=? ORDER BY created_at DESC", (video_id,))
    for r in video["renders"]:
        r["settings"] = json.loads(r.pop("settings_json"))
        r["qc"] = json.loads(r["qc_json"]) if r.get("qc_json") else None
        r.pop("qc_json", None)
    video["jobs"] = db.query(
        "SELECT id, type, status, stage, progress, message, error, created_at, finished_at "
        "FROM jobs WHERE video_id=? ORDER BY created_at DESC LIMIT 10", (video_id,))
    brain_path = config.ARTIFACTS_DIR / video_id / "brain.json"
    video["brain"] = json.loads(brain_path.read_text()) if brain_path.exists() else None
    return video


@router.get("/api/videos/{video_id}/transcript")
def get_transcript(video_id: str):
    path = config.ARTIFACTS_DIR / video_id / "words.json"
    if not path.exists():
        raise HTTPException(404, "not analyzed yet")
    return json.loads(path.read_text())


@router.delete("/api/videos/{video_id}")
def delete_video(video_id: str):
    video = db.query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not video:
        raise HTTPException(404)
    for job in db.query("SELECT id FROM jobs WHERE video_id=? AND status IN ('queued','running')",
                        (video_id,)):
        worker.cancel(job["id"])
    db.execute("DELETE FROM videos WHERE id=?", (video_id,))
    try:
        original_path_for(video_id).unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    shutil.rmtree(config.ARTIFACTS_DIR / video_id, ignore_errors=True)
    shutil.rmtree(config.RENDERS_DIR / video_id, ignore_errors=True)
    return {"deleted": video_id}


@router.get("/api/videos/{video_id}/original")
def get_original(video_id: str):
    try:
        path = original_path_for(video_id)
    except FileNotFoundError:
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


@router.post("/api/videos/{video_id}/analyze")
def reanalyze(video_id: str):
    if not db.query_one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(404)
    settings = RenderSettings.model_validate(db.get_state("last_settings", {}) or {})
    job_id = worker.enqueue(video_id, "analyze", settings, [])
    return {"job_id": job_id}


@router.post("/api/videos/{video_id}/render")
async def render(video_id: str, request: Request):
    video = db.query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not video:
        raise HTTPException(404)
    if video["status"] not in ("analyzed", "ingested"):
        raise HTTPException(409, f"video is {video['status']}")
    body = await request.json()
    try:
        settings = RenderSettings.model_validate(body.get("settings", {}))
    except Exception as e:
        raise HTTPException(422, str(e)[:500])
    variants = [v for v in body.get("variants", list(VARIANTS)) if v in VARIANTS]
    if not variants:
        raise HTTPException(422, "no valid variants")
    job_id = worker.enqueue(video_id, "render", settings, variants)
    return {"job_id": job_id}
