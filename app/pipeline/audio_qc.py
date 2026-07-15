"""Music/ducking loudness QC: measures the ACTUAL mixed output's loudness
with an ffmpeg loudnorm analysis pass (numbers only, no audio input to
Claude -- no vision/audio model call needed here), then asks Claude to reason
about whether music is audible without masking speech and whether ducking is
doing its job. Fills a real gap: nothing in this codebase measures the final
mixed audio today -- ducking is a blind sidechaincompress and the final mix
gets a blind loudnorm pass (filters.py), with no verification either landed
where intended. Follows overlay_qc.py's philosophy: never raises, degrades
to {"checked": False, ...} on any infra hiccup."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .. import config
from ..models import MusicSettings
from .claude_cli import extract_json, invoke_claude

_PROMPT = """You are sanity-checking the MUSIC/DUCKING of an automated
short-form video's final audio mix, using MEASURED loudness numbers (you
cannot hear the audio) -- pure numeric reasoning, not a listening check.

MEASURED (ffmpeg loudnorm analysis on the actual rendered output):
{measured}

SETTINGS THAT PRODUCED THIS MIX:
{settings}

TARGET: integrated loudness should land close to {target_lufs} LUFS (within
about 1.5 LU), true peak at or below {target_tp} dBTP.

Flag a problem ONLY for a genuine, clear issue:
- "too_loud": measured integrated loudness or true peak is meaningfully over
  target -- the mix (likely the music) is probably overwhelming speech.
- "too_quiet": measured integrated loudness is meaningfully under target --
  the mix will sound weak relative to other short-form content.
- "no_ducking_effect": ducking is enabled in the settings but the measured
  loudness range (LRA) is implausibly narrow for a mix where music is
  supposed to duck under voice -- suggests ducking isn't actually engaging.
Minor deviation within the ~1.5 LU tolerance is NOT a problem.

Respond with ONLY a JSON object, no markdown fences:
{{"verdict":"pass"|"fail","failure_mode":"none"|"too_loud"|"too_quiet"|"no_ducking_effect","problem":"...","suggestion":"..."}}
"""


class AudioQCJudgment(BaseModel, extra="forbid"):
    verdict: Literal["pass", "fail"]
    failure_mode: Literal["none", "too_loud", "too_quiet", "no_ducking_effect"] = "none"
    problem: str = ""
    suggestion: str = ""


_JSON_BLOCK_RE = re.compile(r'\{[^{}]*"input_i"[^{}]*\}', re.DOTALL)


def _measure_loudness(out_path: Path, target_lufs: int, target_tp: float, timeout: int = 120) -> dict:
    proc = subprocess.run(
        [config.FFMPEG, "-v", "info", "-i", str(out_path), "-af",
         f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout,
    )
    m = _JSON_BLOCK_RE.search(proc.stderr or "")
    if not m:
        raise RuntimeError("loudnorm did not print measured stats")
    stats = json.loads(m.group(0))
    return {
        "integrated_lufs": float(stats.get("input_i", "nan")),
        "true_peak_dbtp": float(stats.get("input_tp", "nan")),
        "loudness_range_lu": float(stats.get("input_lra", "nan")),
    }


def run_audio_qc(*, out_path: Path, music: MusicSettings, target_lufs: int, target_tp: float) -> dict:
    """Never raises -- degrades to {"checked": False, ...} on any infra
    hiccup, or when no music track is configured at all (nothing to judge --
    ducking-quality checks only make sense when music is actually on)."""
    if not music.track:
        return {"checked": False, "verdict": None, "failure_mode": "none",
                "problem": "", "suggestion": "", "error": "no music track configured", "measured": None}
    try:
        measured = _measure_loudness(out_path, target_lufs, target_tp)
        prompt = _PROMPT.format(
            measured=json.dumps(measured), settings=json.dumps(music.model_dump()),
            target_lufs=target_lufs, target_tp=target_tp)
        text = invoke_claude(prompt)
        judgment = AudioQCJudgment.model_validate(extract_json(text))
        return {"checked": True, "verdict": judgment.verdict, "failure_mode": judgment.failure_mode,
               "problem": judgment.problem, "suggestion": judgment.suggestion, "error": None,
               "measured": measured}
    except Exception as e:
        return {"checked": False, "verdict": None, "failure_mode": "none",
               "problem": "", "suggestion": "", "error": str(e)[:500], "measured": None}
