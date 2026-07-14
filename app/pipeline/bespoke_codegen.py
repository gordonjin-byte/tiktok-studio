"""Bespoke Remotion component generation for overlay cues the advisor flagged
as not fitting any catalog template. Separate, per-cue LLM call (not bundled
into overlay_advisor's batched call) so one cue's compile failure retries
independently without re-running every other bespoke cue.

This is unattended LLM-written code that runs in a background job — the
guardrails here (import allowlist, forbidden-API scan, structural check,
compile-check) must fail closed, never open. On exhausted retries the caller
(run_script_plan) degrades the cue to the generic-caption-card template;
this module only reports success/failure, it doesn't own that fallback."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from .. import config
from .claude_cli import invoke_claude

_ALLOWED_MODULES = {"react", "remotion"}
_FORBIDDEN_PATTERNS = [
    r"\brequire\s*\(", r"\bfetch\s*\(", r"\beval\s*\(", r"\bFunction\s*\(",
    r"\bprocess\.", r"\bchild_process\b", r"\bimport\s*\(", r"\bfs\.",
    r"\bXMLHttpRequest\b", r"\bWebSocket\b", r"\bdocument\.cookie\b",
    r"\bwindow\.location\b", r"\blocalStorage\b",
]
_IMPORT_LINE_RE = re.compile(r'^\s*import\s+.+?\s+from\s+[\'"]([^\'"]+)[\'"];?\s*$', re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```(?:tsx|typescript|ts|jsx)?\s*\n(.*?)```", re.DOTALL)

_PROMPT_TEMPLATE = """You are generating a single Remotion (React/TypeScript)
component for one short animated overlay in a vertical (1080x1920) short-form
video. It will be rendered to a transparent alpha-channel video clip and
composited on top of footage, so anything not part of the graphic must stay
fully transparent.

STRICT CONTRACT — your response must satisfy ALL of these:
- Exactly one component, named `Bespoke`, as the file's default export:
  `export default function Bespoke(props: BespokeProps) {{ ... }}`
- Only these imports are allowed: `react`, `remotion`, `remotion/*`. No other
  package, no `require`, no `fetch`, no `eval`, no filesystem/network/DOM
  storage APIs of any kind — the component must render purely from its props
  and Remotion's own timing hooks (useCurrentFrame, useVideoConfig).
- Use `interpolate`/`spring`/`useCurrentFrame` from `remotion` for animation —
  it must actually animate over its duration, not render a static frame.
- The component receives whatever props are listed below; give each a
  reasonable TypeScript type. Assume canvas is 1080x1920.
- Return ONLY the raw component source code, no markdown fences, no
  explanation before or after.
- Do not use any tools (no web search, no file access, no code execution) —
  everything you need is in this message. Respond directly with the code.

EXAMPLE (shows the expected shape and animation style — write different
content, this is only a style reference):
```tsx
import {{interpolate, spring, useCurrentFrame, useVideoConfig}} from "remotion";

export interface BespokeProps {{
  text: string;
}}

export default function Bespoke({{text}}: BespokeProps) {{
  const frame = useCurrentFrame();
  const {{fps}} = useVideoConfig();
  const scale = spring({{frame, fps, config: {{damping: 12}}}});
  const opacity = interpolate(frame, [0, fps * 0.3], [0, 1], {{extrapolateRight: "clamp"}});
  return (
    <div style={{{{position: "absolute", inset: 0, display: "flex",
                  alignItems: "center", justifyContent: "center"}}}}>
      <div style={{{{transform: `scale(${{scale}})`, opacity, color: "#fff",
                    fontSize: 72, fontWeight: 800, textAlign: "center",
                    padding: 40, textShadow: "0 4px 24px rgba(0,0,0,0.6)"}}}}>
        {{text}}
      </div>
    </div>
  );
}}
```

CREATIVE BRIEF (what this specific overlay should show):
{brief}

EPISODE CONTEXT: {episode_meta}
"""


def _extract_code(text: str) -> str:
    m = _CODE_FENCE_RE.search(text)
    return (m.group(1) if m else text).strip()


def _is_allowed_module(mod: str) -> bool:
    return mod in _ALLOWED_MODULES or mod.startswith("remotion/")


def _static_guardrail_check(code: str) -> Optional[str]:
    for m in _IMPORT_LINE_RE.finditer(code):
        mod = m.group(1)
        if not _is_allowed_module(mod):
            return f"disallowed import {mod!r} — only 'react' and 'remotion'/'remotion/*' are allowed"
    for pat in _FORBIDDEN_PATTERNS:
        if re.search(pat, code):
            return f"disallowed pattern found matching /{pat}/"
    has_named_default = re.search(r"export\s+default\s+function\s+Bespoke\b", code)
    has_const_default = re.search(r"\bconst\s+Bespoke\s*=", code) and re.search(r"export\s+default\s+Bespoke\b", code)
    if not (has_named_default or has_const_default):
        return "must define and export-default a component named Bespoke"
    return None


def _compile_check(file_path: Path) -> tuple[bool, str]:
    validator = config.REMOTION_DIR / "scripts" / "validate-component.mjs"
    try:
        proc = subprocess.run(
            [config.NODE, str(validator), str(file_path)],
            capture_output=True, text=True, timeout=60, cwd=str(config.REMOTION_DIR),
        )
    except Exception as e:
        return False, str(e)[:2000]
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout)[-2000:]


def generate(video_id: str, cue_id: str, bespoke_brief: str, episode_meta: dict,
            max_attempts: int = 2) -> tuple[bool, str, str]:
    """Generates + validates a bespoke component for one cue.
    Returns (success, module_path, error). module_path is relative to
    remotion/src/ (e.g. "generated/{video_id}/{cue_id}"), the same shape
    overlay_advisor.CueRenderSpec.module_path expects."""
    out_dir = config.REMOTION_DIR / "src" / "generated" / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    file_path = out_dir / f"{cue_id}.tsx"
    module_path = f"generated/{video_id}/{cue_id}"

    prompt = _PROMPT_TEMPLATE.format(brief=bespoke_brief, episode_meta=episode_meta)
    last_error = ""
    for attempt in range(max_attempts):
        if attempt > 0:
            # transient CLI failures (empty-stderr exit 1, rate limiting) are
            # more likely to succeed on retry after a short backoff than an
            # immediate re-invocation
            time.sleep(3)
        full_prompt = prompt if attempt == 0 else (
            prompt + f"\n\nYour previous attempt failed:\n{last_error}\n"
                     "Fix it and return ONLY the corrected component source.")
        try:
            text = invoke_claude(full_prompt, timeout_s=config.BESPOKE_CODEGEN_TIMEOUT_S, max_turns=4)
        except Exception as e:
            last_error = str(e)[:1000]
            continue
        code = _extract_code(text)
        guard_err = _static_guardrail_check(code)
        if guard_err:
            last_error = guard_err
            continue
        file_path.write_text(code)
        ok, compile_err = _compile_check(file_path)
        if ok:
            return True, module_path, ""
        last_error = compile_err
    file_path.unlink(missing_ok=True)
    return False, "", last_error
