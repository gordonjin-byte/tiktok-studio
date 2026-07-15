"""Script-driven overlays: paste an episode script, align it to the video's
real transcript, let the overlay advisor + bespoke codegen decide what to
render per cue, review/override those decisions, then render with them.

POST /api/videos/{id}/script requires the video already be analyzed (words.json
/speech_segments.json/brain.json must exist) — script_align needs those, and
this router never runs the analyze/brain pipeline itself, it only enqueues the
"script_plan" job (which reads the already-cached results, see run.py's
load_cached_analysis/load_cached_brain)."""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config, db, events, worker
from ..models import RenderSettings
from ..pipeline import bespoke_codegen, overlay_catalog, overlays, script_parse

router = APIRouter()


def _script_with_cues(script_id: str) -> dict:
    script = db.query_one("SELECT * FROM scripts WHERE id=?", (script_id,))
    if not script:
        raise HTTPException(404, "script not found")
    script["alt_hooks"] = json.loads(script.pop("alt_hooks_json") or "[]")
    script["parsed"] = json.loads(script["parsed_json"]) if script.get("parsed_json") else None
    script["cues"] = db.query(
        "SELECT * FROM script_cues WHERE script_id=? ORDER BY cue_index", (script_id,))
    manifest = overlays.get_manifest(script["video_id"])
    for cue in script["cues"]:
        cue["template_props"] = json.loads(cue.pop("template_props_json") or "{}")
        cue["visual_qc_report"] = json.loads(cue["visual_qc_report"]) if cue.get("visual_qc_report") else None
        entry = manifest.get(cue["id"], {})
        # distinct from decision_status/bespoke_error: this is a Remotion-level
        # render failure (compiled fine, but the actual render_cue.ts batch
        # render failed for this cue) — a separate failure mode from bespoke
        # codegen's own compile/guardrail failures.
        cue["render_error"] = entry.get("error")
        cue["has_preview"] = "error" not in entry and bool(entry)
    return script


def _insert_script_and_cues(video_id: str, raw_text: str) -> str:
    doc = script_parse.parse(raw_text)
    script_id = db.new_id()
    now = db.now()
    duration_estimate = None
    if "duration" in doc.meta:
        duration_estimate = script_parse.parse_duration_seconds(doc.meta["duration"])
    db.insert("scripts", {
        "id": script_id, "video_id": video_id, "raw_text": raw_text,
        "parsed_json": doc.model_dump_json(),
        "episode_title": doc.title, "episode_category": doc.meta.get("category"),
        "episode_difficulty": doc.meta.get("difficulty"),
        "duration_estimate_s": duration_estimate,
        "builds_text": doc.meta.get("builds"), "new_piece_text": doc.meta.get("new_piece"),
        "alt_hooks_json": json.dumps(doc.alt_hooks),
        "status": "parsed",
        "checksum": hashlib.sha256(raw_text.encode()).hexdigest()[:12],
        "created_at": now, "updated_at": now,
    })
    for cue in doc.cues:
        db.insert("script_cues", {
            "id": db.new_id(), "script_id": script_id, "video_id": video_id,
            "cue_index": cue.index, "cue_type": cue.kind, "source_text": cue.text,
            "script_time_s": cue.authored_ts, "decision_status": "pending",
            "advisor_status": "none", "user_overridden": 0,
            "created_at": now, "updated_at": now,
        })
    return script_id


@router.post("/api/videos/{video_id}/script")
async def submit_script(video_id: str, request: Request):
    video = db.query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not video:
        raise HTTPException(404, "video not found")
    if video["status"] not in ("analyzed", "analyzing", "error"):
        raise HTTPException(400, f"video must be analyzed first (status is {video['status']!r})")
    if not (config.ARTIFACTS_DIR / video_id / "words.json").exists():
        raise HTTPException(400, "video has no cached transcript yet — analyze it first")
    body = await request.json()
    raw_text = (body.get("raw_text") or "").strip()
    if not raw_text:
        raise HTTPException(422, "raw_text is required")

    script_id = _insert_script_and_cues(video_id, raw_text)
    settings = RenderSettings.model_validate(db.get_state("last_settings", {}) or {})
    job_id = worker.enqueue(video_id, "script_plan", settings, [], script_id=script_id)
    return {"script_id": script_id, "job_id": job_id}


@router.get("/api/videos/{video_id}/script")
def get_latest_script(video_id: str):
    script = db.query_one(
        "SELECT id FROM scripts WHERE video_id=? ORDER BY created_at DESC LIMIT 1", (video_id,))
    if not script:
        raise HTTPException(404, "no script for this video")
    return _script_with_cues(script["id"])


@router.get("/api/scripts/{script_id}")
def get_script(script_id: str):
    return _script_with_cues(script_id)


@router.post("/api/scripts/{script_id}/plan")
def replan_script(script_id: str):
    script = db.query_one("SELECT * FROM scripts WHERE id=?", (script_id,))
    if not script:
        raise HTTPException(404, "script not found")
    settings = RenderSettings.model_validate(db.get_state("last_settings", {}) or {})
    job_id = worker.enqueue(script["video_id"], "script_plan", settings, [], script_id=script_id)
    return {"job_id": job_id}


@router.patch("/api/scripts/{script_id}/cues/{cue_id}")
async def override_cue(script_id: str, cue_id: str, request: Request):
    cue = db.query_one("SELECT * FROM script_cues WHERE id=? AND script_id=?", (cue_id, script_id))
    if not cue:
        raise HTTPException(404, "cue not found")
    script = db.query_one("SELECT * FROM scripts WHERE id=?", (script_id,))
    body = await request.json()
    decision_kind = body.get("decision_kind")
    if decision_kind not in ("template", "bespoke"):
        raise HTTPException(422, "decision_kind must be 'template' or 'bespoke'")

    now = db.now()
    if decision_kind == "template":
        template_id = body.get("template_id")
        props = body.get("template_props") or {}
        if not template_id:
            raise HTTPException(422, "template_id is required for decision_kind='template'")
        try:
            overlay_catalog.validate_props(template_id, props)
        except Exception as e:
            raise HTTPException(422, f"invalid props for template {template_id!r}: {e}")
        db.update("script_cues", cue_id, {
            "decision_kind": "template", "template_id": template_id,
            "template_props_json": json.dumps(props), "decision_status": "decided",
            "user_overridden": 1, "updated_at": now,
            "visual_qc_status": "none", "visual_qc_report": None, "visual_qc_spec_hash": None,
        })
    else:
        brief = (body.get("bespoke_brief") or "").strip()
        if not brief:
            raise HTTPException(422, "bespoke_brief is required for decision_kind='bespoke'")
        db.update("script_cues", cue_id, {
            "decision_kind": "bespoke", "bespoke_brief": brief,
            "decision_status": "bespoke_pending", "user_overridden": 1, "updated_at": now,
            "visual_qc_status": "none", "visual_qc_report": None, "visual_qc_spec_hash": None,
        })
        episode_meta = {
            "title": script["episode_title"] or "", "category": script["episode_category"] or "",
            "difficulty": script["episode_difficulty"] or "", "builds": script["builds_text"] or "",
            "new_piece": script["new_piece_text"] or "",
        }
        ok, module_path, error = bespoke_codegen.generate(
            script["video_id"], cue_id, brief, episode_meta, duration_s=cue.get("duration_s") or 2.0)
        if ok:
            db.update("script_cues", cue_id, {
                "decision_status": "bespoke_ready", "bespoke_module_path": module_path,
                "bespoke_error": None, "updated_at": db.now()})
        else:
            db.update("script_cues", cue_id, {
                "decision_status": "bespoke_failed", "bespoke_error": error[:2000],
                "updated_at": db.now()})
    events.publish("cue_update", {"video_id": script["video_id"], "script_id": script_id,
                                  "cue_id": cue_id, "decision_status":
                                      db.query_one("SELECT decision_status FROM script_cues WHERE id=?",
                                                   (cue_id,))["decision_status"]})
    return db.query_one("SELECT * FROM script_cues WHERE id=?", (cue_id,))


@router.patch("/api/scripts/{script_id}/cues/{cue_id}/timing")
async def override_cue_timing(script_id: str, cue_id: str, request: Request):
    """Lets the user manually pin which dialogue line a cue's timing should be
    computed from, for when automatic anchoring (script_align.py) picks the
    wrong line. Takes effect on the next plan — this just records the
    override and re-triggers alignment; it doesn't recompute timing inline,
    since alignment needs the cached transcript/brain data run.py already
    knows how to load."""
    cue = db.query_one("SELECT * FROM script_cues WHERE id=? AND script_id=?", (cue_id, script_id))
    if not cue:
        raise HTTPException(404, "cue not found")
    script = db.query_one("SELECT * FROM scripts WHERE id=?", (script_id,))
    body = await request.json()
    line_index = body.get("manual_anchor_line_index")
    if line_index is not None:
        doc = json.loads(script["parsed_json"]) if script.get("parsed_json") else {"lines": []}
        valid_indices = {l["index"] for l in doc.get("lines", [])}
        if line_index not in valid_indices:
            raise HTTPException(422, f"manual_anchor_line_index {line_index} is not a valid dialogue line index")

    db.update("script_cues", cue_id, {
        "manual_anchor_line_index": line_index, "updated_at": db.now(),
        "visual_qc_status": "none", "visual_qc_report": None, "visual_qc_spec_hash": None,
        # a manual pin always wins over the timing agent's skip/anchor call —
        # force it back to "pending" so the advisor actually re-decides it
        # (a previously-skipped cue has no template/bespoke decision at all)
        "overlay_skip": 0, "decision_status": "pending",
    })
    settings = RenderSettings.model_validate(db.get_state("last_settings", {}) or {})
    job_id = worker.enqueue(script["video_id"], "script_plan", settings, [], script_id=script_id)
    return {"job_id": job_id}


@router.post("/api/scripts/{script_id}/cues/{cue_id}/regenerate-bespoke")
def regenerate_bespoke(script_id: str, cue_id: str):
    cue = db.query_one("SELECT * FROM script_cues WHERE id=? AND script_id=?", (cue_id, script_id))
    if not cue:
        raise HTTPException(404, "cue not found")
    if not cue.get("bespoke_brief"):
        raise HTTPException(400, "cue has no bespoke_brief to regenerate from")
    script = db.query_one("SELECT * FROM scripts WHERE id=?", (script_id,))
    episode_meta = {
        "title": script["episode_title"] or "", "category": script["episode_category"] or "",
        "difficulty": script["episode_difficulty"] or "", "builds": script["builds_text"] or "",
        "new_piece": script["new_piece_text"] or "",
    }
    ok, module_path, error = bespoke_codegen.generate(
        script["video_id"], cue_id, cue["bespoke_brief"], episode_meta, duration_s=cue.get("duration_s") or 2.0)
    now = db.now()
    if ok:
        db.update("script_cues", cue_id, {
            # a prior failed attempt degrades decision_kind to "template" (the
            # generic-caption-card fallback) — flip it back to "bespoke" now
            # that codegen actually succeeded, or to_cue_render_specs() would
            # keep using the fallback and silently ignore this module_path.
            "decision_kind": "bespoke",
            "decision_status": "bespoke_ready", "bespoke_module_path": module_path,
            "bespoke_error": None, "updated_at": now,
            "visual_qc_status": "none", "visual_qc_report": None, "visual_qc_spec_hash": None,
        })
        status = "bespoke_ready"
    else:
        db.update("script_cues", cue_id, {
            "decision_status": "bespoke_failed", "bespoke_error": error[:2000], "updated_at": now,
            "visual_qc_status": "none", "visual_qc_report": None, "visual_qc_spec_hash": None,
        })
        status = "bespoke_failed"
    events.publish("cue_update", {"video_id": script["video_id"], "script_id": script_id,
                                  "cue_id": cue_id, "decision_status": status})
    return db.query_one("SELECT * FROM script_cues WHERE id=?", (cue_id,))


@router.delete("/api/scripts/{script_id}")
def delete_script(script_id: str):
    if not db.query_one("SELECT id FROM scripts WHERE id=?", (script_id,)):
        raise HTTPException(404, "script not found")
    # jobs.script_id references scripts(id) without ON DELETE CASCADE/SET NULL
    # (job rows are historical records, never deleted) — detach first so the
    # FK constraint doesn't block deleting the script.
    db.execute("UPDATE jobs SET script_id=NULL WHERE script_id=?", (script_id,))
    db.execute("DELETE FROM scripts WHERE id=?", (script_id,))
    return {"deleted": script_id}


@router.get("/api/templates")
def list_templates(cue_type: str = ""):
    templates = overlay_catalog.templates_for(cue_type) if cue_type else overlay_catalog.all_templates()
    return {"catalog_version": overlay_catalog.catalog_version(), "templates": templates}


@router.get("/api/templates/{template_id}/schema")
def get_template_schema(template_id: str):
    t = overlay_catalog.template(template_id)
    if not t:
        raise HTTPException(404, "template not found")
    return {"template": t, "controls": overlay_catalog.props_schema_ui(template_id)}


@router.get("/api/videos/{video_id}/overlays/{cue_id}/preview")
def get_overlay_preview(video_id: str, cue_id: str):
    """Serves the cue's rendered overlay clip composited onto a checkerboard
    background, transcoded to a browser-playable mp4 — lets you inspect a
    single overlay in isolation (position, legibility, whether it's even
    visible) independent of the final composited render's timing/z-order."""
    try:
        path = overlays.render_preview(video_id, cue_id)
    except FileNotFoundError:
        raise HTTPException(404, "no rendered overlay clip for this cue yet")
    except Exception as e:
        raise HTTPException(500, f"preview generation failed: {e}"[:500])
    return FileResponse(path, media_type="video/mp4")
