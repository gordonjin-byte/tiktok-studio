"""Visual QC for rendered overlay clips: judges 3 still frames of a cue's
rendered overlay (composited onto a checkerboard, via the existing
overlays.render_preview()) against the cue's own creative intent, using
claude_cli.invoke_claude_vision(). Follows qc.py's philosophy exactly — never
raises, degrades to {"checked": False, ...} on any infra hiccup, only a
genuine model verdict of "fail" is actionable. Escalation/retry orchestration
lives in run.py (run_overlay_qc_stage), not here — this module is the pure
"take a rendered clip, return a judgment" half."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from .. import config
from . import overlays
from .claude_cli import extract_json, invoke_claude_vision
from .ffmpeg import ProcHolder, run_cmd

_QC_TIMESTAMPS_FRAC = (0.15, 0.5, 0.85)

_PROMPT_TEMPLATE = """You are doing visual quality-control for one short animated
graphic overlay in a vertical (1080x1920) short-form video. The overlay was
generated automatically (from a template or from custom-generated code) to
depict a specific idea. You are shown {n} still frames captured at different
points across the overlay's {duration_s:.1f}-second on-screen duration
({timestamps}), composited onto a plain checkerboard background so you can see
exactly what is/isn't rendering — the checkerboard itself is just the
background, ignore it, it is not part of the overlay.

WHAT THIS OVERLAY IS SUPPOSED TO SHOW:
"{expected_description}"

Judge it against exactly these four failure modes:
1. BLANK — one or more frames show nothing but the background (no text, no
   shape, no visible content) when the overlay should already be visible at
   that point in its timeline.
2. ILLEGIBLE — content is present but too small, cropped, overlapping, or
   low-contrast to actually read/recognize at a glance on a phone screen.
3. CONTENT MISMATCH — the overlay is legible and non-blank, but clearly
   depicts something different from what's described above (wrong subject,
   wrong objects, wrong visual metaphor — e.g. a generic two-box arrow diagram
   standing in for something that was supposed to show a map, a specific
   object, or a specific place).
4. TOO BRIEF — this clip is only {duration_s:.2f} REAL seconds long, so the 3
   samples above are only ~{sample_gap_s:.2f}s apart in actual time. If the
   description above implies multiple sequential stages/beats (e.g. "X, then
   Y, then Z" or several distinct state changes) but {duration_s:.2f}s is not
   remotely enough real time for a human to register each one distinctly —
   even if each individual still technically shows legible content — that is
   a failure here, not a pass. A single-state cue (one shape, one label) can
   legitimately be fine even at this duration; judge based on how many beats
   THIS specific description implies, not a fixed threshold.

A minor stylistic difference (exact colors, exact wording of an on-screen
label) is NOT a failure — only fail for the four modes above.

Do not use any tools (no web search, no file access, no code execution) — you
have everything you need in this message. Respond with ONLY a JSON object, no
markdown fences, matching exactly:
{{"verdict":"pass"|"fail","failure_mode":"none"|"blank"|"illegible"|"content_mismatch"|"too_brief","problem":"...","suggestion":"..."}}

"problem" must be a one-sentence, concrete description of what is visibly
wrong (empty string if verdict is "pass"). "suggestion" must be one concrete,
actionable instruction for regenerating the overlay to fix it (empty string
if verdict is "pass") — e.g. "show a world map with two labeled points and a
curved dashed line between them, not two boxes."
"""


class VisualQCJudgment(BaseModel, extra="forbid"):
    verdict: Literal["pass", "fail"]
    failure_mode: Literal["none", "blank", "illegible", "content_mismatch", "too_brief"] = "none"
    problem: str = ""
    suggestion: str = ""


def _extract_qc_frames(video_id: str, cue_id: str, duration_s: float,
                       holder: Optional[ProcHolder] = None) -> list[Path]:
    preview_path = overlays.render_preview(video_id, cue_id, holder=holder)
    qc_dir = config.ARTIFACTS_DIR / video_id / "overlays" / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, frac in enumerate(_QC_TIMESTAMPS_FRAC):
        t = max(0.0, min(duration_s * frac, max(0.0, duration_s - 1.0 / config.FPS)))
        out = qc_dir / f"{cue_id}_f{i}.png"
        run_cmd([config.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.3f}",
                 "-i", str(preview_path), "-frames:v", "1", "-vf", "scale=540:-2",
                 str(out)], timeout=60, holder=holder)
        frames.append(out)
    return frames


def run_overlay_visual_qc(*, video_id: str, cue_id: str, expected_description: str,
                          duration_s: float, holder: Optional[ProcHolder] = None) -> dict:
    """Never raises — an infra hiccup (CLI error, timeout, bad JSON) degrades
    to {"checked": False, ...} so it never blocks a cue; only a genuine model
    verdict of 'fail' triggers escalation (handled by run.py)."""
    try:
        frames = _extract_qc_frames(video_id, cue_id, duration_s, holder=holder)
        ts = [duration_s * f for f in _QC_TIMESTAMPS_FRAC]
        sample_gap_s = (max(ts) - min(ts)) / max(1, len(ts) - 1) if len(ts) > 1 else duration_s
        prompt = _PROMPT_TEMPLATE.format(
            n=len(frames), duration_s=duration_s, sample_gap_s=sample_gap_s,
            timestamps=", ".join(f"{t:.2f}s" for t in ts),
            expected_description=expected_description.strip()[:1500])
        text = invoke_claude_vision(frames, prompt, timeout_s=config.VISUAL_QC_TIMEOUT_S)
        judgment = VisualQCJudgment.model_validate(extract_json(text))
        return {"checked": True, "verdict": judgment.verdict, "failure_mode": judgment.failure_mode,
               "problem": judgment.problem, "suggestion": judgment.suggestion,
               "frame_fracs": list(_QC_TIMESTAMPS_FRAC), "error": None}
    except Exception as e:
        return {"checked": False, "verdict": None, "failure_mode": "none",
               "problem": "", "suggestion": "", "frame_fracs": [], "error": str(e)[:500]}
