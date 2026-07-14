"""Shared `claude -p` subprocess invocation, used by brain.py, overlay_advisor.py,
bespoke_codegen.py, and overlay_qc.py. Extracted from brain.py so all
LLM-decision steps shell out to the CLI the same way."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path

from .. import config


def _claude_env() -> dict:
    # launchd/Task Scheduler PATH is minimal — make sure the CLI's own dir
    # (and on macOS, homebrew) are present
    extra_dirs = [os.path.dirname(config.CLAUDE_CLI)]
    if not config.IS_WINDOWS:
        extra_dirs += ["/opt/homebrew/bin", "/usr/bin", "/bin"]
    return {**os.environ,
           "PATH": os.pathsep.join(extra_dirs + [os.environ.get("PATH", "")])}


def _claude_cmd(*extra_args: str) -> list[str]:
    cmd = [config.CLAUDE_CLI, "-p", *extra_args]
    if config.IS_WINDOWS and config.CLAUDE_CLI.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c"] + cmd  # npm shims need the shell
    return cmd


def invoke_claude(prompt: str, timeout_s: float | None = None, max_turns: int = 1) -> str:
    # max_turns=1 is fine for short structured-JSON responses (brain.py,
    # overlay_advisor.py) but a long generated component can apparently need
    # more than one internal turn to complete — hitting the cap there ends
    # the session with terminal_reason="max_turns", which surfaces as a bare
    # non-zero exit (bespoke_codegen.py passes a higher value for this reason).
    cmd = _claude_cmd("--output-format", "json", "--max-turns", str(max_turns))
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        timeout=timeout_s or config.CLAUDE_TIMEOUT_S, env=_claude_env(),
    )
    if proc.returncode != 0:
        # stderr is sometimes empty for CLI-level failures (auth/rate-limit
        # errors can surface as a bare non-zero exit with the detail only in
        # stdout) — fall back to stdout so the error is actually diagnosable.
        detail = proc.stderr.strip() or proc.stdout.strip() or "(no output on stdout or stderr)"
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {detail[-500:]}")
    wrapper = json.loads(proc.stdout)
    if wrapper.get("is_error"):
        raise RuntimeError(f"claude CLI reported is_error: {json.dumps(wrapper)[-500:]}")
    return wrapper.get("result", "")


def invoke_claude_vision(image_paths: list[Path], prompt: str,
                         timeout_s: float | None = None, max_turns: int = 1) -> str:
    """Vision variant of invoke_claude() — for image+text input, `claude -p`
    requires --input-format stream-json PAIRED with --output-format
    stream-json (confirmed: mixing stream-json input with plain json/text
    output fails with an explicit CLI error). The stream-json output is
    MULTIPLE JSON-lines (system/rate_limit/assistant events, then a final
    {"type":"result",...} event) — unlike invoke_claude()'s single JSON blob,
    this must be parsed line-by-line for the last "result" event."""
    content = []
    for p in image_paths:
        data = base64.b64encode(Path(p).read_bytes()).decode("ascii")
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": data}})
    content.append({"type": "text", "text": prompt})
    stdin_data = json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                            separators=(",", ":")) + "\n"

    # --verbose is required by the CLI whenever --print is paired with
    # --output-format=stream-json (confirmed: omitting it is a hard error)
    cmd = _claude_cmd("--input-format", "stream-json", "--output-format", "stream-json",
                      "--verbose", "--max-turns", str(max_turns))
    proc = subprocess.run(
        cmd, input=stdin_data, capture_output=True, text=True,
        timeout=timeout_s or config.CLAUDE_TIMEOUT_S, env=_claude_env(),
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "(no output on stdout or stderr)"
        raise RuntimeError(f"claude CLI vision exit {proc.returncode}: {detail[-500:]}")

    result_text, is_error = None, None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "result":
            is_error = evt.get("is_error")
            result_text = evt.get("result", "")
    if result_text is None:
        raise RuntimeError(f"claude CLI vision: no result event in stream-json output: {proc.stdout[-500:]}")
    if is_error:
        raise RuntimeError(f"claude CLI vision reported is_error: {proc.stdout[-500:]}")
    return result_text


def extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))
