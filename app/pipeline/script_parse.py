"""Deterministic parser for the author's structured episode-script format
(HOOK/PROBLEM/BUILD dialogue lines, ON-SCREEN text cues, an
"OVERLAYS / B-ROLL (timed)" section, an "EFFECTS (timed)" section, ALT HOOKS).

Kept separate from app/models.py deliberately: this describes script *content*,
not tunable render settings, and downstream caching hashes the parsed output,
so parsing must stay deterministic run-to-run for the common well-formed case.
Only escalates to an LLM repair pass when confidence is low."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel

from .claude_cli import extract_json, invoke_claude

_DIALOGUE_RE = re.compile(
    r"^(\d{1,2}):(\d{2})\s+([A-Z][A-Z0-9 +/]*?)\s*[—\-]\s*(.+)$")
_CUE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s+(.+)$")
_ON_SCREEN_RE = re.compile(r'^ON-SCREEN:\s*(.+)$', re.IGNORECASE)
_OVERLAYS_HEADER_RE = re.compile(r"^OVERLAYS\s*/\s*B-ROLL\b", re.IGNORECASE)
_EFFECTS_HEADER_RE = re.compile(r"^EFFECTS\b", re.IGNORECASE)
_ALT_HOOKS_RE = re.compile(r"^ALT HOOKS[^:]*:\s*(.+)$", re.IGNORECASE)
_ALT_HOOK_ITEM_RE = re.compile(r"\d\)\s*")
_QUOTE_CHARS = '"“”\''


def _strip_quotes(s: str) -> str:
    return s.strip().strip(_QUOTE_CHARS).strip()


def _ts_to_seconds(mm: str, ss: str) -> float:
    return int(mm) * 60 + int(ss)


def parse_duration_seconds(s: str) -> Optional[float]:
    m = re.match(r"^~?(\d+\.?\d*)\s*(s|sec|secs|m|min)\b", s.strip(), re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1))
    return val * 60 if m.group(2).lower().startswith("m") else val


class ScriptLine(BaseModel):
    index: int
    label: str
    authored_ts: float
    text: str
    on_screen: Optional[str] = None


class ScriptCue(BaseModel):
    index: int
    kind: Literal["overlay", "effect", "on_screen"]
    authored_ts: float
    text: str
    anchor_line_index: Optional[int] = None


class ScriptDoc(BaseModel):
    title: str = ""
    meta: dict[str, str] = {}
    lines: list[ScriptLine] = []
    cues: list[ScriptCue] = []
    alt_hooks: list[str] = []
    raw_text: str = ""
    parse_method: Literal["deterministic", "llm_repair"] = "deterministic"
    confidence: float = 1.0


def parse(raw_text: str) -> ScriptDoc:
    doc, unmatched, total = _parse_deterministic(raw_text)
    confidence = 1.0 if total == 0 else 1.0 - (unmatched / total)
    doc.confidence = confidence
    low_confidence = (not doc.lines) or (total > 0 and unmatched / total > 0.15)
    if low_confidence:
        try:
            repaired = _parse_via_llm(raw_text)
            repaired.raw_text = raw_text
            repaired.parse_method = "llm_repair"
            repaired.confidence = 1.0
            return repaired
        except Exception:
            pass  # fall through to whatever the deterministic pass produced
    doc.raw_text = raw_text
    return doc


def _parse_deterministic(raw_text: str) -> tuple[ScriptDoc, int, int]:
    lines = raw_text.splitlines()
    idx = 0

    def next_nonblank() -> Optional[str]:
        nonlocal idx
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx >= len(lines):
            return None
        line = lines[idx].strip()
        idx += 1
        return line

    # a script may omit the title/meta header entirely and start straight
    # with a dialogue line (e.g. "0:00 HOOK — ..."); if the very first
    # non-blank line already looks like a dialogue line, section header, or
    # ALT HOOKS line, there is no title to consume — treat it as empty and
    # leave the line for the main state machine below, or the HOOK line
    # itself silently gets swallowed as a bogus "title" and dropped entirely.
    save_idx = idx
    first_line = next_nonblank() or ""
    if (_DIALOGUE_RE.match(first_line) or _OVERLAYS_HEADER_RE.match(first_line)
            or _EFFECTS_HEADER_RE.match(first_line) or _ALT_HOOKS_RE.match(first_line)):
        title = ""
        idx = save_idx
    else:
        title = first_line
    meta: dict[str, str] = {}
    save_idx = idx
    meta_line = next_nonblank()
    if meta_line and "·" in meta_line:
        positional_keys = iter(["category", "difficulty"])
        for part in meta_line.split("·"):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, v = part.split(":", 1)
                meta[k.strip().lower().replace(" ", "_")] = v.strip()
            elif re.match(r"^~?\d+\.?\d*\s*(s|sec|secs|m|min)\b", part, re.IGNORECASE):
                meta["duration"] = part
            else:
                key = next(positional_keys, None)
                meta[key or part.lower().replace(" ", "_")] = part
    else:
        idx = save_idx  # not a meta line — don't consume it

    state = "dialogue"
    script_lines: list[ScriptLine] = []
    overlay_cues: list[ScriptCue] = []
    alt_hooks: list[str] = []
    unmatched = 0
    total = 0

    while idx < len(lines):
        raw = lines[idx]
        idx += 1
        line = raw.strip()
        if not line:
            continue

        if _OVERLAYS_HEADER_RE.match(line):
            state = "overlay"
            continue
        if _EFFECTS_HEADER_RE.match(line):
            state = "effect"
            continue
        m_alt = _ALT_HOOKS_RE.match(line)
        if m_alt:
            for item in _ALT_HOOK_ITEM_RE.split(m_alt.group(1)):
                item = _strip_quotes(item)
                if item:
                    alt_hooks.append(item)
            state = "alt_hooks"
            continue
        if state == "alt_hooks":
            continue  # ignore stray trailing content after ALT HOOKS

        total += 1
        if state == "dialogue":
            m = _DIALOGUE_RE.match(line)
            if m:
                mm, ss, label, text = m.groups()
                script_lines.append(ScriptLine(
                    index=len(script_lines), label=label.strip(),
                    authored_ts=_ts_to_seconds(mm, ss), text=text.strip()))
                continue
            m_os = _ON_SCREEN_RE.match(line)
            if m_os and script_lines:
                script_lines[-1].on_screen = _strip_quotes(m_os.group(1))
                continue
            if script_lines:
                # likely a wrapped continuation of the previous dialogue line
                script_lines[-1].text += " " + line
                continue
            unmatched += 1
        elif state in ("overlay", "effect"):
            m = _CUE_RE.match(line)
            if m:
                mm, ss, text = m.groups()
                overlay_cues.append(ScriptCue(
                    index=len(overlay_cues), kind=state,
                    authored_ts=_ts_to_seconds(mm, ss), text=text.strip()))
                continue
            if overlay_cues:
                overlay_cues[-1].text += " " + line
                continue
            unmatched += 1

    all_cues = list(overlay_cues)
    for ln in script_lines:
        if ln.on_screen:
            all_cues.append(ScriptCue(
                index=len(all_cues), kind="on_screen",
                authored_ts=ln.authored_ts, text=ln.on_screen,
                anchor_line_index=ln.index))
    all_cues.sort(key=lambda c: c.authored_ts)
    for i, c in enumerate(all_cues):
        c.index = i

    doc = ScriptDoc(title=title, meta=meta, lines=script_lines,
                    cues=all_cues, alt_hooks=alt_hooks)
    return doc, unmatched, total


_LLM_REPAIR_PROMPT = """The following is a structured short-form video episode
script. It roughly follows this format (but may have formatting quirks):

TITLE LINE
Category · Difficulty · ~Ns · Builds: ... · New piece: ...
M:SS  LABEL — dialogue text
ON-SCREEN: "on-screen text tied to the previous dialogue line"
...more dialogue lines...
OVERLAYS / B-ROLL (timed)
M:SS  freeform description of a visual overlay/b-roll cue
...
EFFECTS (timed)
M:SS  freeform description of a visual/audio effect cue
...
ALT HOOKS (A/B): 1) "..."  2) "..."

Parse it into ONLY a JSON object, no markdown fences, matching exactly:
{"title":"...","meta":{"category":"...","difficulty":"...","duration":"...","builds":"...","new_piece":"..."},
 "lines":[{"label":"HOOK","authored_ts":0,"text":"...","on_screen":null}],
 "cues":[{"kind":"overlay","authored_ts":1,"text":"..."}],
 "alt_hooks":["...","..."]}

authored_ts is the timestamp converted to seconds (float). kind is "overlay" or
"effect" matching which section the cue line was under. Include every dialogue
line and every cue line found; do not invent content that isn't present.

SCRIPT:
"""


class _LLMLine(BaseModel, extra="forbid"):
    label: str
    authored_ts: float
    text: str
    on_screen: Optional[str] = None


class _LLMCue(BaseModel, extra="forbid"):
    kind: Literal["overlay", "effect"]
    authored_ts: float
    text: str


class _LLMScriptResponse(BaseModel, extra="forbid"):
    title: str = ""
    meta: dict[str, str] = {}
    lines: list[_LLMLine] = []
    cues: list[_LLMCue] = []
    alt_hooks: list[str] = []


def _parse_via_llm(raw_text: str) -> ScriptDoc:
    text = invoke_claude(_LLM_REPAIR_PROMPT + raw_text)
    resp = _LLMScriptResponse.model_validate(extract_json(text))
    script_lines = [
        ScriptLine(index=i, label=l.label, authored_ts=l.authored_ts,
                   text=l.text, on_screen=l.on_screen)
        for i, l in enumerate(resp.lines)
    ]
    all_cues = [
        ScriptCue(index=0, kind=c.kind, authored_ts=c.authored_ts, text=c.text)
        for c in resp.cues
    ]
    for ln in script_lines:
        if ln.on_screen:
            all_cues.append(ScriptCue(
                index=0, kind="on_screen", authored_ts=ln.authored_ts,
                text=ln.on_screen, anchor_line_index=ln.index))
    all_cues.sort(key=lambda c: c.authored_ts)
    for i, c in enumerate(all_cues):
        c.index = i
    return ScriptDoc(title=resp.title, meta=resp.meta, lines=script_lines,
                     cues=all_cues, alt_hooks=resp.alt_hooks)
