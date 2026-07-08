"""Ingest: hash → dedupe key, probe, move into originals/, extract analysis wav."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Optional

from .. import config
from .ffmpeg import ProcHolder, ffprobe_info, run_cmd


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def ingest_file(src: Path, video_id: str) -> dict:
    """Move src into originals/{video_id}{ext} and return probe info."""
    info = ffprobe_info(src)
    if not info["width"] or not info["duration_s"]:
        raise RuntimeError(f"not a decodable video: {src.name}")
    if not info["has_audio"]:
        raise RuntimeError(f"video has no audio track: {src.name}")
    dest = original_path_for(video_id, src.suffix)
    try:
        src.rename(dest)  # same volume
    except OSError:
        shutil.move(str(src), str(dest))
    return info


def original_path_for(video_id: str, ext: str = "") -> Path:
    if ext:
        return config.ORIGINALS_DIR / f"{video_id}{ext.lower()}"
    matches = list(config.ORIGINALS_DIR.glob(f"{video_id}.*"))
    if not matches:
        raise FileNotFoundError(f"original for {video_id} missing")
    return matches[0]


def extract_wav(video_id: str, holder: Optional[ProcHolder] = None) -> Path:
    """16k mono wav for whisper + energy analysis."""
    art = config.ARTIFACTS_DIR / video_id
    art.mkdir(exist_ok=True)
    wav = art / "audio.wav"
    run_cmd([
        config.FFMPEG, "-y", "-v", "error",
        "-i", str(original_path_for(video_id)),
        "-vn", "-ac", "1", "-ar", "16000", str(wav),
    ], timeout=600, holder=holder)
    return wav
