"""Visual QC for burned-in captions: judges frames sampled from the ACTUAL
composited render (captions are burned via the `ass=` filter inside the same
single ffmpeg pass as everything else in render.render_variant() -- there is
no isolated caption layer to preview separately, unlike overlays). Follows
overlay_qc.py's philosophy exactly: never raises, degrades to
{"checked": False, ...} on any infra hiccup, only a genuine "fail" verdict is
actionable. Fixing a caption problem means a full re-render -- orchestration
in run.py bounds this to a single retry given that cost."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from .. import config
from .claude_cli import extract_json, invoke_claude_vision
from .ffmpeg import ProcHolder, run_cmd

_N_SAMPLES = 4

_PROMPT_TEMPLATE = """You are doing visual quality-control for the burned-in
CAPTIONS of a vertical (1080x1920) short-form video. You are shown {n} still
frames sampled from the actual rendered video, each with the caption text
that should be on screen at that instant plus how many real seconds that
specific caption chunk stays on screen:

{chunk_info}

Judge against exactly these two failure modes:
1. ILLEGIBLE -- in one or more frames the caption text is too small, low
   contrast against that frame's actual busy footage, cropped, or otherwise
   hard to read at a glance on a phone screen. Judge the ACTUAL frame you see,
   not a hypothetical -- footage-dependent contrast problems are exactly what
   this check exists to catch.
2. TOO DENSE -- one or more chunks show noticeably more words than a viewer
   could realistically read in that chunk's stated on-screen duration (as a
   rough guide, more than ~3 words per second is often too fast, but judge by
   what you actually see, not just the number).

A minor stylistic preference (exact color, font weight) is NOT a failure --
only fail for the two modes above.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{{"verdict":"pass"|"fail","failure_mode":"none"|"illegible"|"too_dense","problem":"...","suggestion":"..."}}
"""


class CaptionQCJudgment(BaseModel, extra="forbid"):
    verdict: Literal["pass", "fail"]
    failure_mode: Literal["none", "illegible", "too_dense"] = "none"
    problem: str = ""
    suggestion: str = ""


def _pick_sample_chunks(chunks: list[dict], n: int) -> list[dict]:
    if not chunks:
        return []
    if len(chunks) <= n:
        return chunks
    step = len(chunks) / n
    return [chunks[min(len(chunks) - 1, round(i * step))] for i in range(n)]


def _extract_caption_qc_frames(out_path: Path, chunks: list[dict], render_dir: Path,
                               holder: Optional[ProcHolder] = None) -> tuple[list[Path], list[dict]]:
    qc_dir = render_dir / "caption_qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    frames, info = [], []
    for i, c in enumerate(chunks):
        mid = (c["t0"] + c["t1"]) / 2
        out = qc_dir / f"f{i}.png"
        run_cmd([config.FFMPEG, "-y", "-v", "error", "-ss", f"{mid:.3f}",
                 "-i", str(out_path), "-frames:v", "1", "-vf", "scale=540:-2",
                 str(out)], timeout=60, holder=holder)
        frames.append(out)
        info.append({"text": " ".join(w["text"] for w in c["words"]),
                     "on_screen_s": round(c["t1"] - c["t0"], 2)})
    return frames, info


def run_caption_qc(*, out_path: Path, caption_chunks: list[dict], render_dir: Path,
                   holder: Optional[ProcHolder] = None) -> dict:
    """Never raises -- an infra hiccup (CLI error, timeout, bad JSON, no
    chunks) degrades to {"checked": False, ...} so it never blocks a render;
    only a genuine model verdict of 'fail' triggers escalation (run.py)."""
    try:
        sampled = _pick_sample_chunks(caption_chunks, _N_SAMPLES)
        if not sampled:
            return {"checked": False, "verdict": None, "failure_mode": "none",
                    "problem": "", "suggestion": "", "error": "no caption chunks to check"}
        frames, info = _extract_caption_qc_frames(out_path, sampled, render_dir, holder=holder)
        chunk_info = "\n".join(f'- "{c["text"]}" on screen for {c["on_screen_s"]:.2f}s' for c in info)
        prompt = _PROMPT_TEMPLATE.format(n=len(frames), chunk_info=chunk_info)
        text = invoke_claude_vision(frames, prompt, timeout_s=config.VISUAL_QC_TIMEOUT_S)
        judgment = CaptionQCJudgment.model_validate(extract_json(text))
        return {"checked": True, "verdict": judgment.verdict, "failure_mode": judgment.failure_mode,
               "problem": judgment.problem, "suggestion": judgment.suggestion, "error": None}
    except Exception as e:
        return {"checked": False, "verdict": None, "failure_mode": "none",
               "problem": "", "suggestion": "", "error": str(e)[:500]}
