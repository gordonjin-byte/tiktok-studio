"""QC: re-transcribe the rendered output and word-diff it against the EDL's
intended keep-script. A missing run that localizes to a splice boundary means a
clipped word — report which keep-interval to widen so the orchestrator can retry."""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Optional

from .. import config
from .ffmpeg import ProcHolder, run_cmd
from .transcribe import norm_token, transcribe_plain


def run_qc(*, rendered: Path, edl: dict, work_dir: Path,
           holder: Optional[ProcHolder] = None) -> dict:
    wav = work_dir / "qc_audio.wav"
    run_cmd([config.FFMPEG, "-y", "-v", "error", "-i", str(rendered),
             "-vn", "-ac", "1", "-ar", "16000", str(wav)],
            timeout=300, holder=holder)
    heard_words = transcribe_plain(wav, holder=holder)

    expected = [norm_token(w) for w in edl["expected_words"]]
    expected = [w for w in expected if w]
    heard = [norm_token(w["w"]) for w in heard_words]
    heard = [w for w in heard if w]

    sm = difflib.SequenceMatcher(a=expected, b=heard, autojunk=False)
    missing: list[dict] = []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op in ("delete", "replace"):
            miss_words = expected[a0:a1]
            # ASR noise: replacements of equal length are usually mishearings, not clips
            if op == "replace" and (a1 - a0) <= (b1 - b0) + 1:
                continue
            missing.append({"words": miss_words, "expected_index": a0})

    # localize each miss to a keep-interval boundary (± window) for retry widening
    widen: set[int] = set()
    out_words = [w for w in edl["out_words"] if norm_token(w["w"])]
    boundaries = _interval_boundaries_out(edl)
    for miss in missing:
        idx = miss["expected_index"]
        if idx < len(out_words):
            t = out_words[idx]["t0"]
            for iv_idx, times in boundaries.items():
                if any(abs(t - bt) < 0.35 for bt in times):
                    widen.add(iv_idx)
                    miss["near_boundary"] = iv_idx

    ratio = sm.ratio()
    return {
        "match_ratio": round(ratio, 4),
        "missing": missing[:20],
        "widen_intervals": sorted(widen),
        "expected_count": len(expected),
        "heard_count": len(heard),
        "pass": len(missing) == 0 or (ratio > 0.97 and not widen),
    }


def _interval_boundaries_out(edl: dict) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    for i, seg in enumerate(edl["audio_segments"]):
        d = seg["src1"] - seg["src0"]
        out[i] = [seg["out0"], seg["out0"] + d]
    return out
