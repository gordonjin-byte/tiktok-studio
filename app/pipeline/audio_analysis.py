"""Raw audio facts: silence intervals (permissive detector) + 50ms RMS envelope.
No thresholding decisions here — the EDL applies settings to these facts."""
from __future__ import annotations

import json
import math
import re
import struct
import wave
from pathlib import Path
from typing import Optional

from .. import config
from .ffmpeg import ProcHolder

WIN_S = 0.05


def detect_silences(wav: Path, holder: Optional[ProcHolder] = None) -> list[dict]:
    """Permissive silencedetect (-35dB, 0.15s min) — candidates only.
    silencedetect logs to stderr, so this bypasses run_cmd."""
    import subprocess
    if holder:
        holder.check()
    proc = subprocess.run(
        [config.FFMPEG, "-i", str(wav),
         "-af", "silencedetect=noise=-35dB:d=0.15", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    text = proc.stderr
    silences: list[dict] = []
    start: Optional[float] = None
    for line in text.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            silences.append({"t0": start, "t1": float(m.group(1))})
            start = None
    if start is not None:
        silences.append({"t0": start, "t1": None})
    return silences


def rms_envelope(wav: Path) -> list[float]:
    """dBFS per 50ms window."""
    w = wave.open(str(wav))
    n, sr = w.getnframes(), w.getframerate()
    data = struct.unpack(f"<{n}h", w.readframes(n))
    w.close()
    win = int(sr * WIN_S)
    env: list[float] = []
    for i in range(0, n - win + 1, win):
        seg = data[i:i + win]
        rms = math.sqrt(sum(x * x for x in seg) / len(seg))
        env.append(round(20 * math.log10(rms / 32768), 1) if rms > 0 else -99.0)
    return env


def analyze(video_id: str, wav: Path, holder: Optional[ProcHolder] = None) -> dict:
    art = config.ARTIFACTS_DIR / video_id
    (art / "silence.json").write_text(json.dumps(detect_silences(wav, holder)))
    env = rms_envelope(wav)
    (art / "energy.json").write_text(json.dumps({"win_s": WIN_S, "db": env}))
    return {"win_s": WIN_S, "db": env}


# ---- dense audio: whisper timestamps are unreliable across long silences
# (it stretches boundary words into them). Transcribing a silence-stripped
# wav and mapping times back to source gives accurate word timings — the
# technique that fixed clipped-word bugs in the manual session.

DENSE_FLOOR_DB = -38.0
DENSE_PAD_S = 0.15


def fixed_speech_regions(env: list[float]) -> list[list[float]]:
    """Settings-independent regions at a permissive floor, for dense retiming."""
    regions: list[list[float]] = []
    start = None
    for i, db in enumerate(env):
        if db > DENSE_FLOOR_DB:
            if start is None:
                start = i
        elif start is not None:
            regions.append([start * WIN_S, i * WIN_S])
            start = None
    if start is not None:
        regions.append([start * WIN_S, len(env) * WIN_S])
    padded = [[max(0.0, a - DENSE_PAD_S), b + DENSE_PAD_S] for a, b in regions]
    merged: list[list[float]] = []
    for r in padded:
        if merged and r[0] - merged[-1][1] <= 0.3:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] >= 0.15]


DENSE_JOIN_GAP_S = 0.25


def build_dense_wav(wav: Path, regions: list[list[float]], out_wav: Path,
                    holder: Optional[ProcHolder] = None) -> list[dict]:
    """Concat speech regions with a short silence between them (jamming words
    together garbles whisper); returns the dense→source map."""
    from .ffmpeg import run_cmd
    parts, labels = [], []
    dense_map, t = [], 0.0
    for i, (a, b) in enumerate(regions):
        parts.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,"
                     f"apad=pad_dur={DENSE_JOIN_GAP_S}[d{i}]")
        labels.append(f"[d{i}]")
        dense_map.append({"dense0": round(t, 4), "src0": a, "src1": b})
        t += (b - a) + DENSE_JOIN_GAP_S
    script = ";\n".join(parts) + ";\n" + "".join(labels) + f"concat=n={len(regions)}:v=0:a=1[out]\n"
    script_path = out_wav.with_suffix(".filters")
    script_path.write_text(script)
    run_cmd([config.FFMPEG, "-y", "-v", "error", "-i", str(wav),
             "-filter_complex_script", str(script_path),
             "-map", "[out]", str(out_wav)], timeout=300, holder=holder)
    return dense_map


def map_word_span(d0: float, d1: float, dense_map: list[dict]) -> tuple[float, float]:
    """Map a dense word span to source. Whisper sometimes draws a word
    boundary just before a region join, making the word straddle two regions
    (= span crosses real silence). Resolve to the region holding most of the
    word's dense duration."""
    k0 = _region_index(d0, dense_map)
    k1 = _region_index(d1, dense_map)
    if k0 != k1:
        seg0, seg1 = dense_map[k0], dense_map[k1]
        end0 = seg0["dense0"] + (seg0["src1"] - seg0["src0"])
        overlap0 = max(end0 - d0, 0.0)
        overlap1 = max(d1 - seg1["dense0"], 0.0)
        if overlap1 >= overlap0:
            d0 = seg1["dense0"]
        else:
            d1 = end0
    return (dense_to_src(d0, dense_map, "start"), dense_to_src(d1, dense_map, "end"))


def _region_index(t: float, dense_map: list[dict]) -> int:
    for k, seg in enumerate(dense_map):
        end = seg["dense0"] + (seg["src1"] - seg["src0"]) + DENSE_JOIN_GAP_S
        if t < end:
            return k
    return len(dense_map) - 1


def dense_to_src(t: float, dense_map: list[dict], mode: str = "start") -> float:
    """Map a dense-audio time back to source. Boundaries that land in an
    inserted join gap are resolved by role: word STARTS round up to the next
    region's start, word ENDS clamp back to the previous region's end —
    otherwise real source pauses collapse to zero-length gaps."""
    for k, seg in enumerate(dense_map):
        length = seg["src1"] - seg["src0"]
        if t < seg["dense0"] + length:
            return seg["src0"] + max(t - seg["dense0"], 0.0)
        if t < seg["dense0"] + length + DENSE_JOIN_GAP_S:
            if mode == "end" or k + 1 >= len(dense_map):
                return seg["src1"]
            return dense_map[k + 1]["src0"]
    last = dense_map[-1]
    return last["src1"]
