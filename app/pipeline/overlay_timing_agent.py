"""Overlay TIMING agent: one batched `claude -p` call per script, deciding for
each ON-SCREEN/OVERLAY/EFFECT cue the exact WORD (not dialogue line) of the
real transcript where its overlay should begin appearing -- or that the
overlay isn't warranted at all. Chunking the transcript at word granularity
(rather than whisper-sentence/script-line granularity) is what lets an
overlay start mid-sentence, exactly when the relevant concept is actually
said, instead of defaulting to wherever its matched dialogue line happens to
start.

Follows overlay_advisor.py's structural convention (extra="forbid" response
models, validation-retry loop, deterministic fallback so every cue still
resolves to a decision -- "keep whatever heuristic anchor you already had" --
even with zero LLM availability)."""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from pydantic import BaseModel, Field

from .claude_cli import extract_json, invoke_claude

_PROMPT = """You are the overlay-TIMING agent for an automated short-form
video editor. You are given the episode's real transcript, chunked into
individual WORDS each with an index and start time, and a list of visual
overlay/effect cues a human author wants placed somewhere in the video (each
cue is a plain-prose description of what the overlay should show, plus a
rough hint of which nearby words it was already matched to by a heuristic).

For EACH cue, decide exactly which WORD is the right moment for that overlay
to begin appearing: the word at which the speaker starts talking about (or
directly implies) the concept the overlay depicts. Do NOT default to the
start of a sentence or line out of convenience -- read the actual words
around the hint and find the specific moment the concept is introduced,
which is very often mid-sentence, not the first word of it.

If the cue's concept genuinely isn't discussed near its hint words, or the
cue doesn't warrant its own distinct on-screen moment at all (e.g. it just
restates another cue you're also placing, or is too vague to visually
justify appearing at any specific instant), set "skip": true instead of
guessing -- a mistimed or redundant overlay is worse than no overlay.

Respond directly with ONLY a JSON object, no markdown fences, matching exactly:
{"decisions":[
  {"cue_id":"...","skip":false,"anchor_word_index":123,"confidence":0.9,"reason":"..."},
  {"cue_id":"...","skip":true,"anchor_word_index":null,"confidence":0.0,"reason":"why it doesn't need a moment"}
]}

INPUT:
"""


class TimingChoice(BaseModel, extra="forbid"):
    cue_id: str
    skip: bool = False
    anchor_word_index: Optional[int] = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    reason: str = ""


class _TimingPlanResponse(BaseModel, extra="forbid"):
    decisions: list[TimingChoice] = []


class TimingCueInput(BaseModel):
    cue_id: str
    cue_type: str
    source_text: str
    hint_word_start: Optional[int] = None  # heuristic anchor's nearby word index -- scoping hint only
    hint_word_end: Optional[int] = None


class TimingDecision(BaseModel):
    cue_id: str
    skip: bool = False
    anchor_word_index: Optional[int] = None
    confidence: float = 0.0
    reason: str = ""
    status: str = "fallback"  # "agent" | "fallback"


def cue_timing_checksum(cue_id: str, source_text: str, window_sig: str) -> str:
    payload = json.dumps({"cue_id": cue_id, "source_text": source_text, "window": window_sig}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


# ~short-form episode transcripts run a few hundred words; this keeps the
# call comfortably single-shot without needing to window per cue.
_MAX_WORDS_FOR_PROMPT = 4000


def decide_timings(cues: list[TimingCueInput], words: list[dict],
                   use_claude: bool = True) -> tuple[list[TimingDecision], str]:
    """Returns (decisions, status). Never blocks -- on any failure every cue
    gets a fallback decision (skip=False, anchor_word_index=None), which
    callers interpret as "keep the heuristic anchor you already resolved"."""
    if not cues:
        return [], "agent"
    if use_claude:
        try:
            return _claude_decide(cues, words), "agent"
        except Exception:
            pass
    return [TimingDecision(cue_id=c.cue_id, status="fallback") for c in cues], "fallback"


def _claude_decide(cues: list[TimingCueInput], words: list[dict]) -> list[TimingDecision]:
    word_payload = [
        {"i": i, "w": w["w"], "t0": round(w["t0"], 2)}
        for i, w in enumerate(words[:_MAX_WORDS_FOR_PROMPT])
    ]
    payload = {"words": word_payload, "cues": [c.model_dump() for c in cues]}
    prompt = _PROMPT + json.dumps(payload, separators=(",", ":"))
    last_err = ""
    for attempt in range(2):
        text = invoke_claude(prompt if attempt == 0 else
                             prompt + f"\n\nYour previous response failed validation: {last_err}\n"
                                      "Return ONLY the corrected JSON object.")
        try:
            resp = _TimingPlanResponse.model_validate(extract_json(text))
            return _to_decisions(resp, cues, len(word_payload))
        except Exception as e:
            last_err = str(e)[:500]
    raise RuntimeError(f"overlay timing agent failed validation twice: {last_err}")


def _to_decisions(resp: _TimingPlanResponse, cues: list[TimingCueInput], n_words: int) -> list[TimingDecision]:
    by_id = {c.cue_id: c for c in cues}
    decided: dict[str, TimingDecision] = {}
    for d in resp.decisions:
        if d.cue_id not in by_id:
            continue  # agent referenced an unknown cue_id -- ignore
        idx = d.anchor_word_index
        if idx is not None and not (0 <= idx < n_words):
            idx = None  # hallucinated out-of-range index -- treat as unresolved, not a crash
        decided[d.cue_id] = TimingDecision(
            cue_id=d.cue_id, skip=d.skip, anchor_word_index=(idx if not d.skip else None),
            confidence=d.confidence, reason=d.reason, status="agent")
    for c in cues:
        if c.cue_id not in decided:
            decided[c.cue_id] = TimingDecision(cue_id=c.cue_id, status="fallback")
    return [decided[c.cue_id] for c in cues]
