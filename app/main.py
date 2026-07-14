from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, events, watcher, worker
from .api import misc, scripts, videos

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    for name, info in config.binary_report().items():
        marker = "ok" if info["found"] else "MISSING"
        print(f"[startup] {name:<14} {marker:<8} {info['path']}")
    db.get_conn()
    events.init(asyncio.get_running_loop())
    worker.recover_on_startup()
    tasks = [asyncio.create_task(worker.worker_loop()),
             asyncio.create_task(watcher.watch_inbox())]
    yield
    worker.shutdown()
    for t in tasks:
        t.cancel()


app = FastAPI(title="TikTok Studio", lifespan=lifespan)
app.include_router(videos.router)
app.include_router(misc.router)
app.include_router(scripts.router)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
