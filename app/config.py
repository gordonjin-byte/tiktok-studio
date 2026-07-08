"""Central configuration, cross-platform (macOS + Windows).

Binary resolution order: env var override → PATH lookup → platform defaults.
Env overrides: TIKTOKSTUDIO_FFMPEG, TIKTOKSTUDIO_FFPROBE, TIKTOKSTUDIO_WHISPER,
TIKTOKSTUDIO_CLAUDE, TIKTOKSTUDIO_DATA_DIR, TIKTOKSTUDIO_PORT.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

HOME = Path.home()
DATA_DIR = Path(os.environ.get("TIKTOKSTUDIO_DATA_DIR", HOME / "TikTokStudio"))
DB_PATH = DATA_DIR / "db.sqlite3"

INBOX_DIR = DATA_DIR / "inbox"
ORIGINALS_DIR = DATA_DIR / "originals"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
RENDERS_DIR = DATA_DIR / "renders"
MUSIC_DIR = DATA_DIR / "music"
SFX_DIR = DATA_DIR / "sfx"
MODELS_DIR = DATA_DIR / "models"
TOOLS_DIR = DATA_DIR / "tools"  # windows installer drops whisper.cpp etc. here
TMP_DIR = DATA_DIR / "tmp"
LOGS_DIR = DATA_DIR / "logs"

ALL_DIRS = [INBOX_DIR, ORIGINALS_DIR, ARTIFACTS_DIR, RENDERS_DIR,
            MUSIC_DIR, SFX_DIR, MODELS_DIR, TOOLS_DIR, TMP_DIR, LOGS_DIR]


def _resolve(env_key: str, names: list[str], extra_dirs: list[Path]) -> str:
    """env override → PATH → platform-specific candidate dirs. Returns the
    first hit, else the first name (so error messages show what was wanted)."""
    override = os.environ.get(env_key)
    if override:
        return override
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
    for d in extra_dirs:
        for name in names:
            for cand in (d / name, d / f"{name}.exe"):
                if cand.exists():
                    return str(cand)
    return names[0]


if IS_WINDOWS:
    _ffmpeg_dirs = [TOOLS_DIR / "ffmpeg" / "bin", Path("C:/ffmpeg/bin")]
    _whisper_dirs = [TOOLS_DIR / "whisper"]
    _claude_dirs = [HOME / ".local" / "bin",
                    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" ]
    FFMPEG = _resolve("TIKTOKSTUDIO_FFMPEG", ["ffmpeg"], _ffmpeg_dirs)
    FFPROBE = _resolve("TIKTOKSTUDIO_FFPROBE", ["ffprobe"], _ffmpeg_dirs)
    WHISPER_CLI = _resolve("TIKTOKSTUDIO_WHISPER", ["whisper-cli", "main"], _whisper_dirs)
    CLAUDE_CLI = _resolve("TIKTOKSTUDIO_CLAUDE", ["claude", "claude.cmd"], _claude_dirs)
else:
    def _mac_ffbin(env_key: str, name: str) -> str:
        # prefer ffmpeg-full (has libass, required for caption burn) over plain
        # brew ffmpeg, which would otherwise win the PATH lookup
        if os.environ.get(env_key):
            return os.environ[env_key]
        full = Path(f"/opt/homebrew/opt/ffmpeg-full/bin/{name}")
        if full.exists():
            return str(full)
        return _resolve(env_key, [name], [Path("/opt/homebrew/bin"), Path("/usr/local/bin")])

    FFMPEG = _mac_ffbin("TIKTOKSTUDIO_FFMPEG", "ffmpeg")
    FFPROBE = _mac_ffbin("TIKTOKSTUDIO_FFPROBE", "ffprobe")
    WHISPER_CLI = _resolve("TIKTOKSTUDIO_WHISPER", ["whisper-cli"],
                           [Path("/opt/homebrew/bin"), Path("/usr/local/bin")])
    CLAUDE_CLI = _resolve("TIKTOKSTUDIO_CLAUDE", ["claude"], [HOME / ".local" / "bin"])

WHISPER_MODEL = MODELS_DIR / "ggml-small.en.bin"

HOST = "127.0.0.1"
PORT = int(os.environ.get("TIKTOKSTUDIO_PORT", "8765"))

# Bump to invalidate cached analysis artifacts when analysis code changes.
ANALYSIS_VERSION = 1
# Bump when edl/captions/filters change meaningfully (part of settings_hash).
EDL_CODE_VERSION = 1

OUT_W, OUT_H = 1080, 1920
FPS = 30

MIN_FREE_DISK_BYTES = 5 * 1024**3  # refuse new jobs below 5GB free

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mpg", ".avi", ".mkv", ".webm"}

CLAUDE_TIMEOUT_S = 240
WHISPER_TIMEOUT_S = 900
RENDER_TIMEOUT_S = 3600


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for d in ALL_DIRS:
        d.mkdir(exist_ok=True)


def binary_report() -> dict:
    """Resolved tool paths + existence — surfaced at startup and /api/stats
    so a broken install is diagnosable from the browser."""
    out = {}
    for label, path in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE),
                        ("whisper", WHISPER_CLI), ("claude", CLAUDE_CLI)):
        found = Path(path).exists() or shutil.which(path) is not None
        out[label] = {"path": path, "found": found}
    out["whisper_model"] = {"path": str(WHISPER_MODEL), "found": WHISPER_MODEL.exists()}
    return out
