"""Python-side invocation of the Remotion batch overlay renderer. One Node
subprocess per render_overlays stage (not one per cue — amortizes Chrome/Node
cold-start; not one full-length composition — keeps per-cue failure isolation
and fine-grained caching). Follows the same subprocess-invocation shape
app/pipeline/ffmpeg.py already uses for ffmpeg/whisper-cli (stdin_data for
input, run_cmd for the actual Popen/timeout/cancel plumbing)."""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from pathlib import Path

from .. import config
from .ffmpeg import ProcHolder, run_cmd
from .overlay_advisor import CueRenderSpec


def _spec_hash(spec: CueRenderSpec) -> str:
    payload = json.dumps({
        "kind": spec.kind, "template_id": spec.template_id, "module_path": spec.module_path,
        "props": spec.props, "duration_s": spec.duration_s,
        "code_version": config.REMOTION_CODE_VERSION,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _npx_cmd(out_dir) -> list[str]:
    cmd = [config.NPX, "tsx", str(config.REMOTION_DIR / "render" / "render_cue.ts"),
           "--out-dir", str(out_dir)]
    if config.IS_WINDOWS and config.NPX.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c"] + cmd  # npm shims need the shell, same as claude_cli.py
    return cmd


def render_overlay_batch(video_id: str, cue_specs: list[CueRenderSpec],
                         holder: Optional[ProcHolder] = None) -> dict:
    """Writes artifacts/{video_id}/overlays/{cue_id}.mov for each spec whose
    cached spec-hash doesn't already match. Returns
    {"rendered": [{"cue_id"}], "failed": [{"cue_id","error"}], "skipped": [{"cue_id"}]}."""
    overlay_dir = config.ARTIFACTS_DIR / video_id / "overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = overlay_dir / "manifest.json"
    manifest: dict = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    to_render: list[tuple[CueRenderSpec, str]] = []
    skipped: list[dict] = []
    for spec in cue_specs:
        h = _spec_hash(spec)
        clip_path = overlay_dir / f"{spec.cue_id}.mov"
        if manifest.get(spec.cue_id, {}).get("spec_hash") == h and clip_path.exists():
            skipped.append({"cue_id": spec.cue_id})
        else:
            to_render.append((spec, h))

    rendered: list[dict] = []
    failed: list[dict] = []
    if to_render:
        # bare JSON array of CueJob objects, matching remotion/src/lib/types.ts
        jobs = [{
            "cue_id": spec.cue_id, "kind": spec.kind, "template_id": spec.template_id,
            "module_path": spec.module_path, "duration_s": spec.duration_s,
            "fps": config.FPS, "width": config.OUT_W, "height": config.OUT_H,
            "props": spec.props,
        } for spec, _ in to_render]
        stdin_payload = json.dumps(jobs)
        try:
            out = run_cmd(_npx_cmd(overlay_dir), timeout=config.OVERLAY_RENDER_TIMEOUT_S,
                          holder=holder, stdin_data=stdin_payload)
            results = json.loads(out)  # bare array: [{cue_id, status, path?, error?}]
            rendered = [{"cue_id": r["cue_id"]} for r in results if r.get("status") == "ok"]
            failed = [{"cue_id": r["cue_id"], "error": r.get("error", "")}
                     for r in results if r.get("status") != "ok"]
        except Exception as e:
            failed = [{"cue_id": spec.cue_id, "error": str(e)[:500]} for spec, _ in to_render]

        rendered_ids = {r["cue_id"] for r in rendered}
        failed_by_id = {f["cue_id"]: f["error"] for f in failed}
        for spec, h in to_render:
            if spec.cue_id in rendered_ids:
                manifest[spec.cue_id] = {"spec_hash": h, "duration_s": spec.duration_s}
            elif spec.cue_id in failed_by_id:
                # no spec_hash recorded here (on purpose) — a failed render must
                # never be mistaken for a cached success on the next attempt.
                manifest[spec.cue_id] = {"duration_s": spec.duration_s,
                                         "error": failed_by_id[spec.cue_id][:1000]}
        manifest_path.write_text(json.dumps(manifest, indent=1))

    return {"rendered": rendered, "failed": failed, "skipped": skipped}


def get_manifest(video_id: str) -> dict:
    """cue_id -> {"spec_hash"?, "duration_s", "error"?} — used by the API layer
    to tell the UI whether a cue's overlay clip actually rendered (vs failed
    at the Remotion level, distinct from a bespoke-codegen failure)."""
    path = config.ARTIFACTS_DIR / video_id / "overlays" / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else {}


_CHECKER_BG = (
    "color=c=0x1a1a1a:s={w}x{h}:d={dur},"
    "geq=lum='if(mod(floor(X/60)+floor(Y/60),2),90,40)':cb=128:cr=128"
)


def render_preview(video_id: str, cue_id: str, holder: Optional[ProcHolder] = None) -> Path:
    """Composites the cached alpha overlay clip onto a checkerboard background
    and transcodes to a browser-playable H.264 mp4, so a single cue's overlay
    can be inspected in isolation — independent of whether it's correctly
    timed/composited in the final render. Cached alongside the source .mov;
    regenerated if the source is newer (e.g. after a re-plan)."""
    overlay_dir = config.ARTIFACTS_DIR / video_id / "overlays"
    clip_path = overlay_dir / f"{cue_id}.mov"
    if not clip_path.exists():
        raise FileNotFoundError(f"no rendered overlay clip for cue {cue_id}")
    preview_path = overlay_dir / f"{cue_id}.preview.mp4"
    if preview_path.exists() and preview_path.stat().st_mtime >= clip_path.stat().st_mtime:
        return preview_path

    manifest = get_manifest(video_id)
    duration_s = manifest.get(cue_id, {}).get("duration_s") or 2.0
    bg = _CHECKER_BG.format(w=config.OUT_W, h=config.OUT_H, dur=duration_s)
    run_cmd([
        config.FFMPEG, "-y",
        "-f", "lavfi", "-i", bg,
        "-i", str(clip_path),
        "-filter_complex", "[0:v][1:v]overlay=0:0[out]",
        "-map", "[out]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(preview_path),
    ], timeout=60, holder=holder)
    return preview_path
