"""jobs, renders, presets, meta — small routers grouped in one module."""
from __future__ import annotations

import json
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .. import config, db, events, worker
from ..models import RenderSettings, settings_schema

router = APIRouter()


# ---------- jobs ----------

@router.get("/api/jobs")
def list_jobs(video_id: str = "", status: str = "", limit: int = 50):
    where, args = [], []
    if video_id:
        where.append("video_id=?")
        args.append(video_id)
    if status:
        where.append(f"status IN ({','.join('?' * len(status.split(',')))})")
        args.extend(status.split(","))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return {"jobs": db.query(
        f"SELECT * FROM jobs {clause} ORDER BY created_at DESC LIMIT ?", (*args, limit))}


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if not worker.cancel(job_id):
        raise HTTPException(409, "job not cancelable")
    return {"canceled": job_id}


# ---------- renders ----------

@router.get("/api/renders/{render_id}/file")
def render_file(render_id: str):
    r = db.query_one("SELECT * FROM renders WHERE id=?", (render_id,))
    if not r or not r["output_path"]:
        raise HTTPException(404)
    return FileResponse(r["output_path"], media_type="video/mp4",
                        filename=f"{r['video_id']}_{r['variant']}.mp4")


@router.get("/api/renders/{render_id}/poster")
def render_poster(render_id: str):
    r = db.query_one("SELECT video_id FROM renders WHERE id=?", (render_id,))
    if not r:
        raise HTTPException(404)
    path = config.RENDERS_DIR / r["video_id"] / render_id / "poster.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/api/renders/{render_id}/still/{n}")
def render_still(render_id: str, n: int):
    r = db.query_one("SELECT video_id FROM renders WHERE id=?", (render_id,))
    if not r:
        raise HTTPException(404)
    path = config.RENDERS_DIR / r["video_id"] / render_id / "thumbs" / f"still_{n}.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/api/renders/{render_id}/qc")
def render_qc(render_id: str):
    r = db.query_one("SELECT qc_json FROM renders WHERE id=?", (render_id,))
    if not r:
        raise HTTPException(404)
    return json.loads(r["qc_json"]) if r["qc_json"] else {}


# ---------- presets ----------

@router.get("/api/presets")
def list_presets():
    rows = db.query("SELECT * FROM presets ORDER BY name")
    for r in rows:
        r["settings"] = json.loads(r.pop("settings_json"))
    return {"presets": rows, "default_preset": db.get_state("default_preset")}


@router.post("/api/presets")
async def save_preset(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name required")
    settings = RenderSettings.model_validate(body.get("settings", {}))
    existing = db.query_one("SELECT id FROM presets WHERE name=?", (name,))
    if existing:
        db.update("presets", existing["id"],
                  {"settings_json": settings.canonical_json(), "updated_at": db.now()})
        pid = existing["id"]
    else:
        pid = db.new_id()
        db.insert("presets", {"id": pid, "name": name,
                              "settings_json": settings.canonical_json(),
                              "created_at": db.now(), "updated_at": db.now()})
    if body.get("make_default"):
        db.set_state("default_preset", pid)
    return {"id": pid}


@router.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str):
    db.execute("DELETE FROM presets WHERE id=?", (preset_id,))
    if db.get_state("default_preset") == preset_id:
        db.set_state("default_preset", None)
    return {"deleted": preset_id}


# ---------- meta ----------

def _installed_fonts() -> list[str]:
    if config.IS_WINDOWS:
        # standard Windows fonts, verified by their files
        fonts_dir = __import__("pathlib").Path("C:/Windows/Fonts")
        known = {"Arial Black": "ariblk.ttf", "Impact": "impact.ttf",
                 "Verdana": "verdanab.ttf", "Arial": "arialbd.ttf",
                 "Segoe UI Black": "seguibl.ttf"}
        found = [name for name, f in known.items() if (fonts_dir / f).exists()]
        return found or ["Arial Black"]
    candidates = ["Arial Black", "Impact", "Futura", "Helvetica Neue",
                  "Avenir Next Heavy", "Arial Rounded MT Bold", "Verdana"]
    try:
        out = subprocess.run(
            ["/usr/bin/atsutil", "fonts", "-list"], capture_output=True,
            text=True, timeout=10).stdout
        return [f for f in candidates if f in out] or ["Arial Black"]
    except Exception:
        return ["Arial Black"]


_FONTS_CACHE: list[str] = []


@router.get("/api/settings/schema")
def get_schema():
    global _FONTS_CACHE
    if not _FONTS_CACHE:
        _FONTS_CACHE = _installed_fonts()
    music = sorted(p.name for p in config.MUSIC_DIR.iterdir()
                   if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac", ".aac"))
    return {
        "schema": settings_schema(music, _FONTS_CACHE),
        "defaults": RenderSettings().model_dump(),
        "last_settings": db.get_state("last_settings"),
        "variants": {"hook_a": "Cold Open", "hook_b": "Title Card", "hook_c": "Question Hook"},
    }


@router.get("/api/music")
def list_music():
    return {"tracks": sorted(p.name for p in config.MUSIC_DIR.iterdir()
                             if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac", ".aac"))}


@router.get("/api/stats")
def stats():
    usage = shutil.disk_usage(config.DATA_DIR)
    data_size = sum(f.stat().st_size for f in config.DATA_DIR.rglob("*") if f.is_file())
    return {
        "disk_free_gb": round(usage.free / 1e9, 1),
        "data_size_gb": round(data_size / 1e9, 2),
        "queue_depth": db.query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")["n"],
        "video_count": db.query_one("SELECT COUNT(*) AS n FROM videos")["n"],
        "binaries": config.binary_report(),
    }


@router.get("/api/events")
async def sse():
    return StreamingResponse(events.stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
