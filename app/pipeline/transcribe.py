"""whisper-cli wrapper → normalized word list.

Normalization folds whisper's punctuation-only and suffix tokens ('s, 't, …)
into the preceding word, matching the proven session logic."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .. import config
from .ffmpeg import ProcHolder, run_cmd

_SUFFIXES = {"'s", "'t", "'ll", "'re", "'ve", "'d", "'m"}
_PUNCT_RE = re.compile(r"^\s*[\.,\?!':;]+$")


def transcribe_words(wav: Path, out_json: Path, holder: Optional[ProcHolder] = None) -> list[dict]:
    """Run whisper with word-level (-ml 1) timestamps; write normalized words.json."""
    prefix = out_json.with_suffix("")  # whisper appends .json
    run_cmd([
        config.WHISPER_CLI, "-m", str(config.WHISPER_MODEL),
        "-f", str(wav), "-ml", "1", "-oj", "-of", str(prefix),
        "-np",
    ], timeout=config.WHISPER_TIMEOUT_S, holder=holder)
    raw = json.loads(out_json.read_text())
    words = _normalize(raw["transcription"])
    out_json.write_text(json.dumps({"words": words}, indent=1))
    return words


def transcribe_plain(wav: Path, holder: Optional[ProcHolder] = None) -> list[dict]:
    """Segment-level transcription (for QC), normalized to a word list with times."""
    prefix = wav.with_name(wav.stem + "_qc")
    run_cmd([
        config.WHISPER_CLI, "-m", str(config.WHISPER_MODEL),
        "-f", str(wav), "-ml", "1", "-oj", "-of", str(prefix), "-np",
    ], timeout=config.WHISPER_TIMEOUT_S, holder=holder)
    raw = json.loads(prefix.with_suffix(".json").read_text())
    return _normalize(raw["transcription"])


def _normalize(segments: list[dict]) -> list[dict]:
    words: list[dict] = []
    for seg in segments:
        text = seg["text"]
        t0 = seg["offsets"]["from"] / 1000.0
        t1 = seg["offsets"]["to"] / 1000.0
        if not text.strip():
            continue
        stripped = text.strip()
        is_suffix = (_PUNCT_RE.match(text) or stripped.lower() in _SUFFIXES
                     or (not text.startswith(" ") and words))
        if is_suffix and words:
            words[-1]["w"] += stripped
            words[-1]["t1"] = t1
            continue
        words.append({"w": stripped, "t0": t0, "t1": t1})
    return words


def norm_token(w: str) -> str:
    """Lowercased, punctuation-stripped token for comparisons."""
    return re.sub(r"[^a-z0-9']", "", w.lower())
