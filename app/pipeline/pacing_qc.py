"""Cut-pacing sanity check: judges whether the EDL's cut pattern reads as
reasonable short-form pacing, using text-only reasoning over shot-length/cut
telemetry already computed by build_edl() -- no rendered video needed, so
this runs BEFORE the (expensive) render step. Follows overlay_qc.py's
philosophy (never raises, degrades to {"checked": False, ...} on any infra
hiccup) but is ADVISORY-ONLY for v1: a "fail" verdict is surfaced in the
dashboard, not auto-fed back into brain.py's retake decisions -- that would
risk fighting the existing QC_MAX_RETRIES/boundary_padding word-clip retry
loop in run.py's run_render_variant(), which already mutates cut boundaries
for a different reason (clipped words, not pacing feel)."""
from __future__ import annotations

import json
import statistics
from typing import Literal

from pydantic import BaseModel

from .claude_cli import extract_json, invoke_claude

_PROMPT = """You are sanity-checking the EDIT PACING of an automated
short-form video edit -- not the content, just whether the pattern of cuts
reads as reasonable short-form pacing or has an obvious problem.

You are given: the list of shot lengths (seconds) in cut order, shot count,
median shot length, total video duration, cuts-per-minute, and the reasons
the retake-removal step decided to cut what it cut.

Flag a problem ONLY for a genuine, obvious pacing issue:
- "too_choppy": an unnatural cluster of very rapid cuts (several very short
  shots in a row) with no discernible reason from the retake reasons given.
- "uneven": one or more shots dramatically longer than the rest (e.g. one
  static shot several times the median in an otherwise fast-cut video) that
  likely reads as a dead spot.
Minor natural variation in shot length is NOT a problem -- only flag a clear
outlier pattern a viewer would actually notice.

Do not use any tools -- respond with ONLY a JSON object, no markdown fences:
{"verdict":"pass"|"fail","failure_mode":"none"|"too_choppy"|"uneven","problem":"...","suggestion":"..."}

INPUT:
"""


class PacingJudgment(BaseModel, extra="forbid"):
    verdict: Literal["pass", "fail"]
    failure_mode: Literal["none", "too_choppy", "uneven"] = "none"
    problem: str = ""
    suggestion: str = ""


def _shot_stats(edl: dict) -> dict:
    segs = edl.get("video_segments", [])
    lengths = [round(s["src1"] - s["src0"], 2) for s in segs if s.get("src1", 0) > s.get("src0", 0)]
    total = edl.get("total_out_s", 0.0)
    cuts_per_min = (len(edl.get("cut_times_out", [])) / total * 60) if total else 0.0
    return {
        "shot_lengths_s": lengths,
        "shot_count": len(lengths),
        "median_shot_s": round(statistics.median(lengths), 2) if lengths else 0.0,
        "total_duration_s": round(total, 2),
        "cuts_per_minute": round(cuts_per_min, 1),
    }


def run_pacing_qc(*, edl: dict, retake_reasons: list[str]) -> dict:
    """Never raises -- degrades to {"checked": False, ...} on any infra
    hiccup or when there's not enough signal (too few shots) to judge."""
    stats = _shot_stats(edl)
    if stats["shot_count"] < 3:
        return {"checked": False, "verdict": None, "failure_mode": "none",
               "problem": "", "suggestion": "", "error": "too few shots to judge pacing"}
    try:
        payload = {**stats, "retake_reasons": retake_reasons[:20]}
        prompt = _PROMPT + json.dumps(payload, separators=(",", ":"))
        text = invoke_claude(prompt)
        judgment = PacingJudgment.model_validate(extract_json(text))
        return {"checked": True, "verdict": judgment.verdict, "failure_mode": judgment.failure_mode,
               "problem": judgment.problem, "suggestion": judgment.suggestion, "error": None}
    except Exception as e:
        return {"checked": False, "verdict": None, "failure_mode": "none",
               "problem": "", "suggestion": "", "error": str(e)[:500]}
