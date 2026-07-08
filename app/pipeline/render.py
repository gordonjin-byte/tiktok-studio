"""Assemble and run the final ffmpeg command for one variant; extract stills."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .. import config
from ..models import RenderSettings
from .captions import build_ass
from .ffmpeg import ProcHolder, run_cmd
from .filters import build_filter_script


def render_variant(*, edl: dict, settings: RenderSettings, video_id: str,
                   render_dir: Path, holder: Optional[ProcHolder] = None,
                   progress_cb: Optional[Callable[[float], None]] = None) -> Path:
    render_dir.mkdir(parents=True, exist_ok=True)
    import json
    (render_dir / "edl.json").write_text(json.dumps(edl, indent=1))

    ass_path = render_dir / "captions.ass"
    build_ass(edl, settings, ass_path)

    music_path = config.MUSIC_DIR / settings.music.track if settings.music.track else None
    script_path = render_dir / "filters.txt"
    meta = build_filter_script(
        edl, settings, script_path,
        music_path=music_path,
        sfx_whoosh=config.SFX_DIR / "whoosh.wav",
        sfx_pop=config.SFX_DIR / "pop.wav",
        ass_path=ass_path,
    )

    from .ingest import original_path_for
    src = original_path_for(video_id)
    out = render_dir / "out.mp4"

    args = [config.FFMPEG, "-y", "-v", "error", "-progress", "pipe:1",
            "-i", str(src)]
    for extra in meta["inputs"]:
        args += ["-i", extra]
    args += [
        "-filter_complex_script", str(script_path),
        "-map", meta["v_label"], "-map", meta["a_label"],
        "-c:v", "libx264", "-crf", str(settings.output.crf),
        "-preset", settings.output.preset, "-pix_fmt", "yuv420p",
        "-r", str(config.FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        str(out),
    ]
    log = render_dir / "ffmpeg.log"
    run_cmd(args, timeout=config.RENDER_TIMEOUT_S, holder=holder,
            log_path=log, progress_cb=progress_cb, total_s=edl["total_out_s"])
    if not out.exists() or out.stat().st_size < 10000:
        raise RuntimeError("render produced no output; see ffmpeg.log")
    return out


def extract_stills(video: Path, out_dir: Path, n: int = 6,
                   duration_s: float = 60.0, holder: Optional[ProcHolder] = None) -> list[Path]:
    out_dir.mkdir(exist_ok=True)
    paths = []
    for i in range(n):
        t = duration_s * (i + 0.5) / n
        p = out_dir / f"still_{i}.jpg"
        run_cmd([config.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.2f}",
                 "-i", str(video), "-frames:v", "1", "-vf", "scale=270:-2", str(p)],
                timeout=120, holder=holder)
        paths.append(p)
    return paths


def extract_poster(video: Path, out_path: Path, t: float = 0.5,
                   holder: Optional[ProcHolder] = None) -> Path:
    run_cmd([config.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.2f}",
             "-i", str(video), "-frames:v", "1", "-vf", "scale=540:-2", str(out_path)],
            timeout=120, holder=holder)
    return out_path
