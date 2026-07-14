"""Overlay/effect "advisor": one batched `claude -p` call per script, deciding
for each ON-SCREEN/OVERLAY/EFFECT cue whether an existing Remotion template
fits (and with what props) or whether the cue needs bespoke-generated Remotion
code. Follows brain.py's exact structural convention (extra="forbid" response
models, validation-retry loop, deterministic heuristic fallback so a cue
always resolves to something renderable even with zero LLM availability)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel

from . import overlay_catalog
from .claude_cli import extract_json, invoke_claude

_PROMPT = """You are the overlay-design advisor for an automated short-form
video editor. The video is a coding/tech explainer series ("build a system,
one concept per episode"). You are given the episode's metadata, a catalog of
reusable animated overlay templates, and a list of cues — each cue is either
an ON-SCREEN text card, an OVERLAY/B-ROLL description, or an EFFECT
description, written by the human author in plain prose.

For EACH cue, decide:
- "template": an existing catalog template plausibly matches the cue's intent.
  Pick its id and fill "props" to match that template's props_schema exactly
  (required fields must be present; use the cue's own wording for any text
  props — do not invent unrelated content).
- "bespoke": no catalog template fits well enough. Write a short creative
  brief (bespoke_brief) describing exactly what the animation should show, and
  a suggested_duration_s. This will be handed to a separate step that
  generates custom Remotion code from your brief, so be concrete and visual.

Prefer "template" whenever a reasonable fit exists — bespoke generation is
slower and less reliable, so reserve it for genuinely unusual cues.

Do not use any tools (no web search, no file access, no code execution) — you
have everything you need in this message. Respond directly with ONLY a JSON
object, no markdown fences, matching exactly:
{"decisions":[
  {"cue_id":"...","kind":"template","template":{"template_id":"...","props":{...}},"reason":"..."},
  {"cue_id":"...","kind":"bespoke","bespoke":{"bespoke_brief":"...","suggested_duration_s":2.0},"reason":"..."}
]}

INPUT:
"""


class TemplateChoice(BaseModel, extra="forbid"):
    template_id: str
    props: dict[str, Any] = {}


class BespokeChoice(BaseModel, extra="forbid"):
    bespoke_brief: str
    suggested_duration_s: float = 2.5


class _CueDecisionResp(BaseModel, extra="forbid"):
    cue_id: str
    kind: Literal["template", "bespoke"]
    template: Optional[TemplateChoice] = None
    bespoke: Optional[BespokeChoice] = None
    reason: str = ""


class _OverlayPlanResponse(BaseModel, extra="forbid"):
    decisions: list[_CueDecisionResp] = []


class CueInput(BaseModel):
    cue_id: str
    cue_type: Literal["on_screen", "overlay", "effect"]
    source_text: str
    nearby_dialogue: str = ""
    available_duration_s: float = 2.5


class CueDecision(BaseModel):
    cue_id: str
    kind: Literal["template", "bespoke"]
    template_id: Optional[str] = None
    props: dict[str, Any] = {}
    bespoke_brief: Optional[str] = None
    duration_s: float = 2.0
    reason: str = ""
    advisor_status: Literal["claude", "fallback"] = "fallback"


class CueRenderSpec(BaseModel, extra="forbid"):
    cue_id: str
    kind: Literal["template", "bespoke"]
    template_id: Optional[str] = None
    module_path: Optional[str] = None
    props: dict[str, Any] = {}
    duration_s: float
    z_index: int = 0


def cue_advisor_checksum(source_text: str, nearby_dialogue: str, episode_meta: dict) -> str:
    payload = json.dumps({
        "source_text": source_text, "nearby_dialogue": nearby_dialogue,
        "episode_meta": episode_meta, "catalog_version": overlay_catalog.catalog_version(),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def plan_cues(cues: list[CueInput], episode_meta: dict,
             use_claude: bool = True) -> tuple[list[CueDecision], str]:
    """Returns (decisions, status) where status is 'claude' or 'fallback'.
    Decisions are always returned for every input cue — never blocks."""
    if not cues:
        return [], "claude"
    if use_claude:
        try:
            decisions = _claude_plan(cues, episode_meta)
            return decisions, "claude"
        except Exception:
            pass
    return _fallback_plan(cues), "fallback"


def _claude_plan(cues: list[CueInput], episode_meta: dict) -> list[CueDecision]:
    cue_types = {c.cue_type for c in cues}
    catalog_slice = [
        {"id": t["id"], "title": t.get("title", t["id"]), "description": t.get("description", ""),
         "match_hints": t.get("match_hints", []), "applicable_cue_types": t.get("applicable_cue_types", []),
         "props_schema": t.get("props_schema", {}), "default_duration_s": t.get("default_duration_s", 2.0)}
        for t in overlay_catalog.all_templates()
        if any(ct in t.get("applicable_cue_types", []) for ct in cue_types)
    ]
    payload = {
        "episode": episode_meta,
        "catalog": catalog_slice,
        "cues": [c.model_dump() for c in cues],
    }
    prompt = _PROMPT + json.dumps(payload, separators=(",", ":"))
    last_err = ""
    for attempt in range(2):
        text = invoke_claude(prompt if attempt == 0 else
                             prompt + f"\n\nYour previous response failed validation: {last_err}\n"
                                      "Return ONLY the corrected JSON object.")
        try:
            resp = _OverlayPlanResponse.model_validate(extract_json(text))
            return _to_cue_decisions(resp, cues)
        except Exception as e:
            last_err = str(e)[:500]
    raise RuntimeError(f"overlay advisor failed validation twice: {last_err}")


def _to_cue_decisions(resp: _OverlayPlanResponse, cues: list[CueInput]) -> list[CueDecision]:
    by_id = {c.cue_id: c for c in cues}
    decided: dict[str, CueDecision] = {}
    for d in resp.decisions:
        cue = by_id.get(d.cue_id)
        if cue is None:
            continue  # advisor referenced an unknown cue_id — ignore
        if d.kind == "template" and d.template is not None:
            try:
                overlay_catalog.validate_props(d.template.template_id, d.template.props)
                t = overlay_catalog.template(d.template.template_id)
                duration = min(t.get("default_duration_s", cue.available_duration_s) if t else cue.available_duration_s,
                               cue.available_duration_s or 999)
                decided[cue.cue_id] = CueDecision(
                    cue_id=cue.cue_id, kind="template", template_id=d.template.template_id,
                    props=d.template.props, duration_s=duration, reason=d.reason,
                    advisor_status="claude")
            except Exception:
                decided[cue.cue_id] = _fallback_one(cue)  # bad props for this cue only — don't fail the batch
        elif d.kind == "bespoke" and d.bespoke is not None:
            decided[cue.cue_id] = CueDecision(
                cue_id=cue.cue_id, kind="bespoke", bespoke_brief=d.bespoke.bespoke_brief,
                duration_s=d.bespoke.suggested_duration_s, reason=d.reason, advisor_status="claude")
    # any cue the advisor never mentioned still gets a decision
    for cue in cues:
        if cue.cue_id not in decided:
            decided[cue.cue_id] = _fallback_one(cue)
    return [decided[c.cue_id] for c in cues]


_STOPWORDS = set("a an and the of to in on with for as at is are was were it its this that".split())


def _fallback_plan(cues: list[CueInput]) -> list[CueDecision]:
    return [_fallback_one(c) for c in cues]


def _fallback_one(cue: CueInput) -> CueDecision:
    candidates = overlay_catalog.templates_for(cue.cue_type)
    cue_tokens = {t for t in re.findall(r"[a-z']+", cue.source_text.lower()) if t not in _STOPWORDS}
    best, best_score = None, 0
    for t in candidates:
        hint_tokens = {w for h in t.get("match_hints", []) for w in h.lower().split()}
        score = len(cue_tokens & hint_tokens)
        if score > best_score:
            best_score, best = score, t
    if best is None:
        best = overlay_catalog.template(overlay_catalog.fallback_template_id()) or {
            "id": "generic-caption-card", "props_schema": {"required": ["text"], "properties": {"text": {"type": "string"}}},
            "default_duration_s": 1.5,
        }
    props = _trivial_props(best, cue.source_text)
    duration = min(best.get("default_duration_s", 2.0), cue.available_duration_s or 999)
    return CueDecision(cue_id=cue.cue_id, kind="template", template_id=best["id"],
                       props=props, duration_s=duration, reason="heuristic fallback",
                       advisor_status="fallback")


_QUOTED_RE = re.compile(r'"([^"]{2,80})"')


def _trivial_props(t: dict, source_text: str) -> dict:
    schema = t.get("props_schema", {})
    required = schema.get("required", [])
    props_spec = schema.get("properties", {})
    quoted = _QUOTED_RE.findall(source_text)
    text_fallback = quoted[0] if quoted else source_text
    props: dict[str, Any] = {}
    for key in required:
        spec = props_spec.get(key, {})
        if "enum" in spec and spec["enum"]:
            props[key] = spec["enum"][0]
        elif spec.get("type") == "boolean":
            props[key] = False
        elif spec.get("type") in ("number", "integer"):
            props[key] = spec.get("minimum", 0)
        elif spec.get("pattern", "").startswith("^#"):
            props[key] = "#FFFFFF"
        else:
            props[key] = text_fallback[: spec.get("maxLength", 80)]
    return props


def to_cue_render_specs(script_cues_rows: list[dict]) -> list[CueRenderSpec]:
    """DB script_cues rows -> CueRenderSpec list, the render-time contract
    worker.py hands to overlays.render_overlay_batch()."""
    specs: list[CueRenderSpec] = []
    for row in script_cues_rows:
        if row.get("decision_status") not in ("decided", "bespoke_ready", "bespoke_failed"):
            continue
        kind = row.get("decision_kind")
        if kind == "template":
            specs.append(CueRenderSpec(
                cue_id=row["id"], kind="template", template_id=row["template_id"],
                props=json.loads(row["template_props_json"] or "{}"),
                duration_s=row.get("duration_s") or 2.0,
            ))
        elif kind == "bespoke" and row.get("bespoke_module_path"):
            specs.append(CueRenderSpec(
                cue_id=row["id"], kind="bespoke", module_path=row["bespoke_module_path"],
                props=json.loads(row["template_props_json"] or "{}"),
                duration_s=row.get("duration_s") or 2.0,
            ))
    return specs
