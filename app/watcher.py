"""Inbox watcher: drop a video into ~/TikTokStudio/inbox and it gets ingested
and fully processed. Size-stable-across-two-polls detection avoids grabbing
files mid-copy."""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import config, db, worker
from .models import RenderSettings, VARIANTS
from .pipeline import ingest


async def watch_inbox() -> None:
    sizes: dict[Path, int] = {}
    while True:
        try:
            for path in sorted(config.INBOX_DIR.iterdir()):
                if not path.is_file() or path.name.startswith("."):
                    continue
                if path.suffix.lower() not in config.VIDEO_EXTENSIONS:
                    continue
                size = path.stat().st_size
                if sizes.get(path) == size and size > 0:
                    sizes.pop(path, None)
                    await asyncio.to_thread(ingest_and_enqueue, path)
                else:
                    sizes[path] = size
        except Exception as e:
            print(f"[watcher] {e}")
        await asyncio.sleep(2)


def ingest_and_enqueue(path: Path, filename: str | None = None) -> dict:
    """Shared by watcher and upload API. Moves `path` into originals/."""
    display_name = filename or path.name
    sha = ingest.sha256_file(path)
    existing = db.query_one("SELECT * FROM videos WHERE sha256=?", (sha,))
    if existing:
        path.unlink(missing_ok=True)  # duplicate drop
        return {"video_id": existing["id"], "duplicate": True, "job_id": None}
    video_id = db.new_id()
    info = ingest.ingest_file(path, video_id)
    db.insert("videos", {
        "id": video_id, "filename": display_name, "sha256": sha,
        "duration_s": info["duration_s"], "width": info["width"],
        "height": info["height"], "fps": info["fps"], "size_bytes": info["size_bytes"],
        "status": "ingested", "created_at": db.now(),
    })
    settings = _default_settings()
    job_id = worker.enqueue(video_id, "full", settings, list(VARIANTS))
    return {"video_id": video_id, "duplicate": False, "job_id": job_id}


def _default_settings() -> RenderSettings:
    """Default preset if set, else last-used settings, else defaults."""
    import json
    preset_id = db.get_state("default_preset")
    if preset_id:
        row = db.query_one("SELECT settings_json FROM presets WHERE id=?", (preset_id,))
        if row:
            return RenderSettings.model_validate(json.loads(row["settings_json"]))
    return RenderSettings.model_validate(db.get_state("last_settings", {}) or {})
