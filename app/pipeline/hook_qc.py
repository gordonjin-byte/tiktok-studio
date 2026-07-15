"""Per-variant hook QC. The 3 render variants are NOT 3 competing hook texts
for one slot -- BrainResult.hooks (models.py) has three fixed, structurally
different fields (cold_open_caption / title_card / question_banner), each
mapped to one specific, differently-styled variant (hook_a/b/c -- see
edl.py's lead_in/HOOK_WINDOW_S branching and captions.py's TitleCard/QBanner
styling). So there's no "pick the best of 3" -- this judges whether each
variant's OWN hook field reads well in its OWN style, extracting frames from
that variant's own rendered opening window. Follows overlay_qc.py's
philosophy: never raises, degrades to {"checked": False, ...} on any infra
hiccup, only a genuine "fail" verdict is actionable."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from .. import config
from .claude_cli import extract_json, invoke_claude, invoke_claude_vision
from .ffmpeg import ProcHolder, run_cmd

# variant -> (HookTexts field name, style label used only in the prompt)
VARIANT_HOOK_FIELD = {
    "hook_a": "cold_open_caption",
    "hook_b": "title_card",
    "hook_c": "question_banner",
}

_N_FRAMES = 3

_PROMPT_TEMPLATE = """You are doing visual quality-control for the OPENING
HOOK of one variant of an automated short-form video, vertical 1080x1920.
This variant's hook style is "{style}" and its text is:
"{hook_text}"

You are shown {n} still frames sampled across the opening ~{window_s:.1f}
seconds where this hook text is on screen.

Judge against exactly these two failure modes:
1. ILLEGIBLE -- the hook text is cropped, overlapping other elements, too
   small, or low-contrast against the actual footage in one or more frames.
2. WEAK_HOOK -- the text itself, in this style, is unlikely to stop a scroll
   (too vague, doesn't state a concrete stake/question/promise) -- judge the
   WRITING, not just the rendering, but only fail for a genuinely weak hook,
   not a stylistic preference.

Respond with ONLY a JSON object, no markdown fences:
{{"verdict":"pass"|"fail","failure_mode":"none"|"illegible"|"weak_hook","problem":"...","suggestion":"..."}}
"""

_REGEN_PROMPT = """You are rewriting ONE hook text field for an automated
short-form video editor. The style is "{style}" ({style_desc}). The current
text is: "{current_text}"

A quality check found this problem: {problem}
Suggested fix: {suggestion}

Episode context (first ~40 words of the real transcript): {transcript_snippet}

Write a single replacement for this field only, matching its style/length
constraints ({constraints}). Respond with ONLY the replacement text, no
quotes, no markdown, no explanation.
"""

_STYLE_DESC = {
    "cold_open_caption": ("a punchy restatement of the opening line", "<=8 words"),
    "title_card": ("a short title", "<=6 words"),
    "question_banner": ("the premise phrased as a question", "<=9 words"),
}


class HookQCJudgment(BaseModel, extra="forbid"):
    verdict: Literal["pass", "fail"]
    failure_mode: Literal["none", "illegible", "weak_hook"] = "none"
    problem: str = ""
    suggestion: str = ""


def _extract_hook_frames(out_path: Path, window_s: float, render_dir: Path,
                         holder: Optional[ProcHolder] = None) -> list[Path]:
    qc_dir = render_dir / "hook_qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(_N_FRAMES):
        t = max(0.0, window_s * (i + 0.5) / _N_FRAMES)
        out = qc_dir / f"f{i}.png"
        run_cmd([config.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.3f}",
                 "-i", str(out_path), "-frames:v", "1", "-vf", "scale=540:-2",
                 str(out)], timeout=60, holder=holder)
        frames.append(out)
    return frames


def run_hook_qc(*, out_path: Path, variant: str, hook_text: str, window_s: float,
                render_dir: Path, holder: Optional[ProcHolder] = None) -> dict:
    """Never raises -- an infra hiccup degrades to {"checked": False, ...}
    so it never blocks a render; only a genuine 'fail' triggers escalation
    (run.py)."""
    try:
        style = VARIANT_HOOK_FIELD.get(variant, "cold_open_caption")
        frames = _extract_hook_frames(out_path, window_s, render_dir, holder=holder)
        prompt = _PROMPT_TEMPLATE.format(style=style, hook_text=hook_text,
                                         n=len(frames), window_s=window_s)
        text = invoke_claude_vision(frames, prompt, timeout_s=config.VISUAL_QC_TIMEOUT_S)
        judgment = HookQCJudgment.model_validate(extract_json(text))
        return {"checked": True, "verdict": judgment.verdict, "failure_mode": judgment.failure_mode,
               "problem": judgment.problem, "suggestion": judgment.suggestion, "error": None}
    except Exception as e:
        return {"checked": False, "verdict": None, "failure_mode": "none",
               "problem": "", "suggestion": "", "error": str(e)[:500]}


def regenerate_hook_field(*, field_name: str, current_text: str, problem: str,
                          suggestion: str, transcript_snippet: str) -> Optional[str]:
    """Returns the replacement text, or None on any failure (caller keeps
    the existing text -- never blocks a render over a hook rewrite)."""
    style_desc, constraints = _STYLE_DESC.get(field_name, ("a short hook", "<=8 words"))
    try:
        prompt = _REGEN_PROMPT.format(
            style=field_name, style_desc=style_desc, current_text=current_text,
            problem=problem or "(none stated)", suggestion=suggestion or "(none stated)",
            transcript_snippet=transcript_snippet[:300], constraints=constraints)
        text = invoke_claude(prompt).strip().strip('"')
        return text if text else None
    except Exception:
        return None
