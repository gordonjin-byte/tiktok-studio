"""Subprocess wrappers, cross-platform (POSIX + Windows). Pipeline code is
synchronous; the worker runs jobs in a thread and cancels by killing the
process tree registered in ProcHolder."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .. import config

IS_WINDOWS = sys.platform == "win32"

if not IS_WINDOWS:
    import signal


def _popen_kwargs() -> dict:
    """Start children in their own group/session so we can kill the whole tree."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_proc_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            # /T kills the whole tree; there is no killpg equivalent
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, subprocess.SubprocessError):
        pass


class PipelineCancelled(Exception):
    pass


class ProcHolder:
    """Shared between worker (cancel) and pipeline (registration)."""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        proc = self.proc
        if proc:
            kill_proc_tree(proc)

    def check(self) -> None:
        if self.cancelled:
            raise PipelineCancelled()


def run_cmd(args: list[str], *, timeout: int, holder: Optional[ProcHolder] = None,
            log_path: Optional[Path] = None,
            progress_cb: Optional[Callable[[float], None]] = None,
            total_s: Optional[float] = None,
            stdin_data: Optional[str] = None) -> str:
    """Run a command; returns stdout. If progress_cb+total_s given, args should
    include `-progress pipe:1` and progress is parsed from stdout lines."""
    if holder:
        holder.check()
    log_f = open(log_path, "ab") if log_path else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=log_f if log_path else subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            text=True,
            **_popen_kwargs(),
        )
        if holder:
            holder.proc = proc
        if stdin_data is not None:
            try:
                proc.stdin.write(stdin_data)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        out_lines: list[str] = []
        if progress_cb and total_s:
            for line in proc.stdout:
                out_lines.append(line)
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                        progress_cb(min(us / 1_000_000 / total_s, 1.0))
                    except ValueError:
                        pass
            proc.wait(timeout=timeout)
        else:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_proc_tree(proc)
                raise
            out_lines = [stdout or ""]
            if proc.returncode != 0 and not log_path:
                raise RuntimeError(
                    f"command failed ({proc.returncode}): {' '.join(args[:3])}...\n"
                    f"{(stderr or '')[-2000:]}")
        if holder:
            holder.proc = None
            if holder.cancelled:
                raise PipelineCancelled()
        if proc.returncode != 0:
            tail = ""
            if log_path and log_path.exists():
                tail = log_path.read_bytes()[-2000:].decode(errors="replace")
            raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args[:3])}...\n{tail}")
        return "".join(out_lines)
    finally:
        if log_path:
            log_f.close()


def ffprobe_info(path: Path) -> dict:
    out = run_cmd([
        config.FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], timeout=60)
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fr = v.get("r_frame_rate", "30/1")
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "duration_s": float(data.get("format", {}).get("duration", 0) or 0),
        "size_bytes": int(data.get("format", {}).get("size", 0) or 0),
        "width": int(v.get("width", 0) or 0),
        "height": int(v.get("height", 0) or 0),
        "fps": fps,
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }
