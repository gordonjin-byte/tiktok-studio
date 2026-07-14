"""Editorial judgment: shell out to `claude -p` for take selection, keyword
highlights, and hook text. Falls back to heuristics so the pipeline never blocks."""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Optional

from .. import config
from pydantic import BaseModel

from ..models import BrainResult, HookTexts, KeywordPick, RetakeDecision
from .claude_cli import extract_json, invoke_claude
from .transcribe import norm_token

_STOPWORDS = set("""a an and are as at be but by for from has have he her his i if in into is it its
just me my not of on or our out she so that the their them they this to up was we what when which
who will with you your it's that's here's don't can't won't""".split())

_PROMPT = """You are the editorial brain of an automated short-form video editor.
The input is a word-indexed transcript of a teleprompter talking-head recording.
The speaker re-reads flubbed lines (retakes); the raw footage contains every take.
Candidate retake groups (near-duplicate phrases) are provided as hints but may be
incomplete — read the whole transcript and find ALL repeated/flubbed content.

Decide the edit:
- drops: word-index ranges [first_i, last_i] (inclusive) to REMOVE. For each piece of
  repeated content keep exactly one delivery — usually the LAST and most complete take
  (prefer the one with extra content) — and drop the rest, including partial/garbled
  earlier attempts. The final kept transcript must read as one clean, non-repeating script.
- keywords: 8-16 word indices to highlight in captions — the punchiest concept words,
  at most one per phrase. Pick indices of KEPT words only.
- hooks (tailored to the content):
  cold_open_caption (<=8 words, punchy restatement of the opening),
  title_card (<=6 words, a title),
  question_banner (<=9 words, question form of the premise).
- banner_text: <=6 words series-style banner (topic + format). cta_text: <=5 words
  follow CTA; if the outro teases a next episode, reference it.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{"drops":[{"words":[first_i,last_i],"reason":"..."}],
 "keywords":[{"word_index":12,"word":"POLLING"}],
 "hooks":{"cold_open_caption":"...","title_card":"...","question_banner":"..."},
 "banner_text":"...","cta_text":"..."}

INPUT:
"""


class _ClaudeDrop(BaseModel, extra="forbid"):
    words: list[int]
    reason: str = ""


class _ClaudeResponse(BaseModel, extra="forbid"):
    drops: list[_ClaudeDrop] = []
    keywords: list[KeywordPick] = []
    hooks: HookTexts
    banner_text: str = "MY SERIES"
    cta_text: str = "FOLLOW FOR MORE"


def words_checksum(words: list[dict], segments: dict | None = None) -> str:
    import hashlib
    payload = json.dumps(words, sort_keys=True).encode()
    if segments is not None:
        payload += json.dumps(segments.get("sentences", []), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def run_brain(video_id: str, words: list[dict], segments: dict,
              filename_hint: str = "", use_claude: bool = True) -> tuple[BrainResult, str]:
    """Returns (result, status) where status is 'claude' or 'fallback'."""
    art = config.ARTIFACTS_DIR / video_id
    checksum = words_checksum(words, segments)
    if use_claude:
        try:
            result = _claude_brain(words, segments, filename_hint)
            (art / "brain.json").write_text(json.dumps(
                {"status": "claude", "words_checksum": checksum, **result.model_dump()}, indent=1))
            return result, "claude"
        except Exception as e:
            (art / "brain_error.txt").write_text(str(e)[:4000])
    result = _fallback_brain(words, segments)
    (art / "brain.json").write_text(json.dumps(
        {"status": "fallback", "words_checksum": checksum, **result.model_dump()}, indent=1))
    return result, "fallback"


def load_brain(video_id: str, words: list[dict],
               segments: dict | None = None) -> Optional[tuple[BrainResult, str]]:
    """Cached brain, valid only for the transcript+segmentation it came from."""
    path = config.ARTIFACTS_DIR / video_id / "brain.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.pop("words_checksum", None) != words_checksum(words, segments):
        return None
    status = data.pop("status", "fallback")
    return BrainResult.model_validate(data), status


def _claude_brain(words: list[dict], segments: dict, filename_hint: str) -> BrainResult:
    payload = {
        "filename": filename_hint,
        "phrases": [
            {"text": s["text"], "first_word_index": s["word_indices"][0],
             "last_word_index": s["word_indices"][-1]}
            for s in segments["sentences"]
        ],
        "words": [{"i": i, "w": w["w"]} for i, w in enumerate(words)],
        "retake_group_hints": [
            [x["text"] for x in g["sentences"]] for g in segments["retake_groups"]
        ],
    }
    prompt = _PROMPT + json.dumps(payload, separators=(",", ":"))
    last_err = ""
    for attempt in range(2):
        text = invoke_claude(prompt if attempt == 0 else
                             prompt + f"\n\nYour previous response failed validation: {last_err}\n"
                                      "Return ONLY the corrected JSON object.")
        try:
            resp = _ClaudeResponse.model_validate(extract_json(text))
            return _to_brain_result(resp, words)
        except Exception as e:
            last_err = str(e)[:500]
    raise RuntimeError(f"claude brain failed validation twice: {last_err}")


_FILLERS = {"and", "but", "so", "or", "um", "uh", "like"}


def _to_brain_result(resp: _ClaudeResponse, words: list[dict]) -> BrainResult:
    """Word-index drop ranges → source-time spans (with small pause padding
    so the cut swallows the hesitation around a flubbed take)."""
    n = len(words)
    retakes = []
    for gid, d in enumerate(resp.drops):
        if len(d.words) != 2:
            raise ValueError(f"drop range must be [first,last]: {d.words}")
        i0, i1 = sorted((max(0, d.words[0]), min(n - 1, d.words[1])))
        # seam polish: if the words just before the drop repeat as the first
        # kept words after it ("instead, we [DROP] instead, we open…"),
        # absorb the pre-duplicate into the drop
        for k in (3, 2, 1):
            pre = [norm_token(w["w"]) for w in words[max(i0 - k, 0):i0]]
            post = [norm_token(w["w"]) for w in words[i1 + 1:i1 + 1 + k]]
            if len(pre) == k and pre == post:
                i0 -= k
                break
        # absorb an orphaned conjunction/filler left dangling right after a drop
        while (i1 - i0) >= 2 and i1 + 2 < n and \
                norm_token(words[i1 + 1]["w"]) in _FILLERS:
            i1 += 1
        t0 = words[i0]["t0"]
        t1 = words[i1]["t1"]
        # extend into surrounding pauses, but never into adjacent kept words
        prev_end = words[i0 - 1]["t1"] if i0 > 0 else 0.0
        next_start = words[i1 + 1]["t0"] if i1 + 1 < n else t1 + 10
        t0 = max(prev_end + 0.03, t0 - 0.3)
        t1 = min(next_start - 0.03, t1 + 0.3)
        retakes.append(RetakeDecision(
            group_id=gid, keep_span=[t0, t1],
            drop_spans=[[round(t0, 3), round(max(t1, t0 + 0.05), 3)]],
            reason=d.reason))
    keywords = [k for k in resp.keywords if 0 <= k.word_index < n]
    return BrainResult(retakes=retakes, keywords=keywords, hooks=resp.hooks,
                       banner_text=resp.banner_text, cta_text=resp.cta_text)


def _fallback_brain(words: list[dict], segments: dict) -> BrainResult:
    sentences = segments["sentences"]

    # "keep the last utterance of any repeated content": right-to-left sweep —
    # drop sentence i if a later KEPT sentence within 45s repeats it. This
    # catches whole-take retakes, short fragments ("Any now?"), and earlier
    # partial deliveries subsumed by a fuller later take.
    toks = []
    for s in sentences:
        ts = [norm_token(w) for w in s["text"].split()]
        toks.append([t for t in ts if t])
    kept_flags = [True] * len(sentences)
    for i in range(len(sentences) - 1, -1, -1):
        if len(toks[i]) < 2:
            continue
        for j in range(i + 1, len(sentences)):
            if not kept_flags[j] or len(toks[j]) < 2:
                continue
            if sentences[j]["t0"] - sentences[i]["t1"] > 45:
                break
            inter = len(set(toks[i]) & set(toks[j]))
            if inter / len(set(toks[i])) >= 0.6 and inter >= 2:
                kept_flags[i] = False
                break

    retakes = []
    gid = 0
    for i, s in enumerate(sentences):
        if not kept_flags[i]:
            retakes.append(RetakeDecision(
                group_id=gid,
                keep_span=[s["t0"], s["t1"]],  # informational; drop is what matters
                drop_spans=[[s["t0"], s["t1"]]],
                reason="heuristic: repeated later",
            ))
            gid += 1

    # keywords: rarest meaningful word per sentence
    counts = Counter(norm_token(w["w"]) for w in words)
    keywords: list[KeywordPick] = []
    for s in sentences:
        best, best_score = None, 0.0
        for i in s["word_indices"]:
            tok = norm_token(words[i]["w"])
            if len(tok) < 4 or tok in _STOPWORDS:
                continue
            score = len(tok) / counts[tok]
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            keywords.append(KeywordPick(word_index=best, word=words[best]["w"]))

    first = sentences[0]["text"] if sentences else "Watch this"
    first_words = re.sub(r"[\.\?!,]", "", first).split()
    hooks = HookTexts(
        cold_open_caption=" ".join(first_words[:8]),
        title_card=" ".join(first_words[:6]),
        question_banner=("What if " + " ".join(first_words[:6]) + "?")[:60],
    )
    return BrainResult(retakes=retakes, keywords=keywords[:16], hooks=hooks,
                       banner_text="MY SERIES", cta_text="FOLLOW FOR MORE")
