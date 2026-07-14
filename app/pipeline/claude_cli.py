"""Shared `claude -p` subprocess invocation, used by brain.py, overlay_advisor.py,
and bespoke_codegen.py. Extracted from brain.py so all three LLM-decision steps
shell out to the CLI the same way."""
from __future__ import annotations

import json
import os
import re
import subprocess

from .. import config


def invoke_claude(prompt: str, timeout_s: float | None = None, max_turns: int = 1) -> str:
    # launchd/Task Scheduler PATH is minimal — make sure the CLI's own dir
    # (and on macOS, homebrew) are present
    extra_dirs = [os.path.dirname(config.CLAUDE_CLI)]
    if not config.IS_WINDOWS:
        extra_dirs += ["/opt/homebrew/bin", "/usr/bin", "/bin"]
    env = {**os.environ,
           "PATH": os.pathsep.join(extra_dirs + [os.environ.get("PATH", "")])}
    # max_turns=1 is fine for short structured-JSON responses (brain.py,
    # overlay_advisor.py) but a long generated component can apparently need
    # more than one internal turn to complete — hitting the cap there ends
    # the session with terminal_reason="max_turns", which surfaces as a bare
    # non-zero exit (bespoke_codegen.py passes a higher value for this reason).
    cmd = [config.CLAUDE_CLI, "-p", "--output-format", "json", "--max-turns", str(max_turns)]
    if config.IS_WINDOWS and config.CLAUDE_CLI.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c"] + cmd  # npm shims need the shell
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        timeout=timeout_s or config.CLAUDE_TIMEOUT_S, env=env,
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


def extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))
