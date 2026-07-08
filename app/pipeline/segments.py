"""Facts layer: sentences from words, and candidate retake groups
(clusters of near-duplicate sentences — the speaker re-reading a line).
No decisions here; the brain/EDL decide what to keep."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config
from .transcribe import norm_token


def build_sentences(words: list[dict]) -> list[dict]:
    """Split the word stream into phrase units. Word times are accurate
    (dense retiming), so real pauses are the primary boundary signal —
    whisper often emits comma run-ons instead of sentence periods."""
    sentences: list[dict] = []
    cur: list[int] = []
    for i, w in enumerate(words):
        cur.append(i)
        gap = words[i + 1]["t0"] - w["t1"] if i + 1 < len(words) else 99.0
        terminal = re.search(r"[\.\?!]$", w["w"]) is not None
        comma = w["w"].endswith(",")
        if terminal or gap > 0.6 or (comma and gap > 0.4):
            sentences.append(_mk_sentence(words, cur))
            cur = []
    if cur:
        sentences.append(_mk_sentence(words, cur))
    return sentences


def _mk_sentence(words: list[dict], idxs: list[int]) -> dict:
    return {
        "word_indices": idxs,
        "text": " ".join(words[i]["w"] for i in idxs),
        "t0": words[idxs[0]]["t0"],
        "t1": words[idxs[-1]]["t1"],
    }


def _similarity(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    return inter / max(len(sa), len(sb))


def find_retake_groups(words: list[dict], sentences: list[dict]) -> list[dict]:
    """Cluster sentences whose token sets overlap heavily → candidate retakes.
    Only nearby repetitions count (retakes happen within ~60s of each other)."""
    toks = [[norm_token(words[i]["w"]) for i in s["word_indices"]] for s in sentences]
    toks = [[t for t in ts if t] for ts in toks]
    groups: list[list[int]] = []
    assigned: dict[int, int] = {}
    for i in range(len(sentences)):
        if len(toks[i]) < 3:
            continue
        for j in range(i + 1, len(sentences)):
            if len(toks[j]) < 3:
                continue
            if sentences[j]["t0"] - sentences[i]["t1"] > 60:
                break
            if _similarity(toks[i], toks[j]) >= 0.6:
                gi = assigned.get(i)
                if gi is None:
                    gi = len(groups)
                    groups.append([i])
                    assigned[i] = gi
                if j not in assigned:
                    groups[gi].append(j)
                    assigned[j] = gi
    out = []
    for gid, sent_idxs in enumerate(groups):
        out.append({
            "group_id": gid,
            "sentences": [
                {"sentence_index": si, "text": sentences[si]["text"],
                 "t0": sentences[si]["t0"], "t1": sentences[si]["t1"]}
                for si in sent_idxs
            ],
        })
    return out


def build_segments(video_id: str, words: list[dict]) -> dict:
    sentences = build_sentences(words)
    retake_groups = find_retake_groups(words, sentences)
    result = {"sentences": sentences, "retake_groups": retake_groups}
    art = config.ARTIFACTS_DIR / video_id
    (art / "speech_segments.json").write_text(json.dumps(result, indent=1))
    return result
