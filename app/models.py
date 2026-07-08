"""Pydantic models: the settings schema (single source of truth for UI + renderer),
brain contract, and job/EDL shapes."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from . import config

Color = str  # "#RRGGBB"


class CaptionSettings(BaseModel, extra="forbid"):
    enabled: bool = True
    font: str = "Arial Black"
    size: int = Field(86, ge=40, le=140)
    color: Color = "#FFFFFF"
    highlight_color: Color = "#FFD400"
    outline_width: int = Field(8, ge=0, le=14)
    position_v: int = Field(560, ge=120, le=1400)
    max_words_per_chunk: int = Field(3, ge=1, le=5)
    max_chars_per_chunk: int = Field(15, ge=8, le=30)
    uppercase: bool = True
    animation: Literal["pop", "fade", "none"] = "pop"
    highlight_keywords: bool = True


class CutSettings(BaseModel, extra="forbid"):
    min_pause_kept_ms: int = Field(250, ge=80, le=1200)
    pad_ms: int = Field(60, ge=0, le=200)
    silence_floor_db: int = Field(-35, ge=-50, le=-20)
    retake_removal: bool = True
    crossfade_ms: int = Field(15, ge=5, le=40)


class ZoomSettings(BaseModel, extra="forbid"):
    enabled: bool = True
    level: float = Field(1.06, ge=1.0, le=1.3)
    frequency: Literal["low", "medium", "high"] = "medium"
    hook_punch: bool = True


class AudioSettings(BaseModel, extra="forbid"):
    loudness_lufs: int = Field(-14, ge=-20, le=-10)
    true_peak_db: float = Field(-1.5, ge=-3.0, le=-1.0)


class MusicSettings(BaseModel, extra="forbid"):
    track: Optional[str] = None  # filename in music/ or None = off
    volume_db: int = Field(-26, ge=-40, le=-10)
    ducking: bool = True
    duck_amount: Literal["light", "medium", "heavy"] = "medium"


class SfxSettings(BaseModel, extra="forbid"):
    whoosh_on_zoom: bool = True
    pop_on_hook: bool = True
    volume_db: int = Field(-18, ge=-30, le=-6)


class ColorSettings(BaseModel, extra="forbid"):
    preset: Literal["none", "punchy", "warm", "cool", "bw"] = "punchy"


class OverlaySettings(BaseModel, extra="forbid"):
    banner_enabled: bool = True
    banner_text: str = ""  # "" = use brain suggestion
    cta_enabled: bool = True
    cta_text: str = ""  # "" = use brain suggestion
    cta_last_seconds: int = Field(5, ge=3, le=10)


class HookSettings(BaseModel, extra="forbid"):
    text_override: Optional[str] = None  # None = brain-generated


class OutputSettings(BaseModel, extra="forbid"):
    crf: int = Field(19, ge=16, le=28)
    preset: Literal["fast", "medium", "slow"] = "medium"


class RenderSettings(BaseModel, extra="forbid"):
    captions: CaptionSettings = CaptionSettings()
    cuts: CutSettings = CutSettings()
    zoom: ZoomSettings = ZoomSettings()
    audio: AudioSettings = AudioSettings()
    music: MusicSettings = MusicSettings()
    sfx: SfxSettings = SfxSettings()
    color: ColorSettings = ColorSettings()
    overlays: OverlaySettings = OverlaySettings()
    hook: HookSettings = HookSettings()
    output: OutputSettings = OutputSettings()

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))

    def settings_hash(self, analysis_version: int) -> str:
        payload = f"{self.canonical_json()}|av={analysis_version}|ev={config.EDL_CODE_VERSION}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------- brain contract ----------

class RetakeDecision(BaseModel, extra="forbid"):
    group_id: int
    keep_span: list[float]  # [start_s, end_s] in source time
    drop_spans: list[list[float]]
    reason: str = ""


class KeywordPick(BaseModel, extra="forbid"):
    word_index: int
    word: str = ""


class HookTexts(BaseModel, extra="forbid"):
    cold_open_caption: str
    title_card: str
    question_banner: str


class BrainResult(BaseModel, extra="forbid"):
    retakes: list[RetakeDecision] = []
    keywords: list[KeywordPick] = []
    hooks: HookTexts
    banner_text: str = "MY SERIES"
    cta_text: str = "FOLLOW FOR MORE"


VARIANTS = ("hook_a", "hook_b", "hook_c")
VARIANT_LABELS = {"hook_a": "Cold Open", "hook_b": "Title Card", "hook_c": "Question Hook"}


# ---------- UI schema export ----------

_LABELS: dict[str, str] = {
    "captions.enabled": "Captions", "captions.font": "Font", "captions.size": "Size",
    "captions.color": "Text color", "captions.highlight_color": "Highlight color",
    "captions.outline_width": "Outline", "captions.position_v": "Vertical position",
    "captions.max_words_per_chunk": "Max words/chunk", "captions.max_chars_per_chunk": "Max chars/chunk",
    "captions.uppercase": "Uppercase", "captions.animation": "Animation",
    "captions.highlight_keywords": "Keyword highlights",
    "cuts.min_pause_kept_ms": "Min pause kept (ms)", "cuts.pad_ms": "Boundary padding (ms)",
    "cuts.silence_floor_db": "Silence floor (dB)", "cuts.retake_removal": "Remove retakes",
    "cuts.crossfade_ms": "Audio crossfade (ms)",
    "zoom.enabled": "Punch-in zooms", "zoom.level": "Zoom level", "zoom.frequency": "Zoom frequency",
    "zoom.hook_punch": "Hook punch-in",
    "audio.loudness_lufs": "Loudness (LUFS)", "audio.true_peak_db": "True peak (dB)",
    "music.track": "Track", "music.volume_db": "Music volume (dB)", "music.ducking": "Duck under voice",
    "music.duck_amount": "Duck amount",
    "sfx.whoosh_on_zoom": "Whoosh on cuts", "sfx.pop_on_hook": "Pop on hook", "sfx.volume_db": "SFX volume (dB)",
    "color.preset": "Grade preset",
    "overlays.banner_enabled": "Top banner", "overlays.banner_text": "Banner text (blank = AI)",
    "overlays.cta_enabled": "End CTA", "overlays.cta_text": "CTA text (blank = AI)",
    "overlays.cta_last_seconds": "CTA duration (s)",
    "hook.text_override": "Hook text (blank = AI)",
    "output.crf": "Quality (CRF, lower=better)", "output.preset": "Encode speed",
}

_STEPS = {"zoom.level": 0.01, "audio.true_peak_db": 0.1}
_COLOR_KEYS = {"captions.color", "captions.highlight_color"}
_DYNAMIC_ENUMS = {"music.track": "music", "captions.font": "fonts"}


def settings_schema(music_tracks: list[str], fonts: list[str]) -> list[dict[str, Any]]:
    """Flatten RenderSettings into UI control descriptors."""
    out: list[dict[str, Any]] = []
    defaults = RenderSettings()
    for group_name, group_field in RenderSettings.model_fields.items():
        group_model = group_field.default if group_field.default is not None else group_field.annotation()
        for key, field in type(group_model).model_fields.items():
            full = f"{group_name}.{key}"
            default = getattr(getattr(defaults, group_name), key)
            entry: dict[str, Any] = {
                "key": full, "group": group_name,
                "label": _LABELS.get(full, key.replace("_", " ").title()),
                "default": default,
            }
            ann = field.annotation
            origin = getattr(ann, "__origin__", None)
            if full in _COLOR_KEYS:
                entry["type"] = "color"
            elif full in _DYNAMIC_ENUMS:
                entry["type"] = "enum"
                opts = music_tracks if _DYNAMIC_ENUMS[full] == "music" else fonts
                entry["options"] = ([None] + opts) if full == "music.track" else opts
            elif origin is Literal:
                entry["type"] = "enum"
                entry["options"] = list(ann.__args__)
            elif ann is bool:
                entry["type"] = "bool"
            elif ann in (int, float) or origin is not None and ann.__args__[0] in (int, float):
                entry["type"] = "number"
                for meta in field.metadata:
                    if hasattr(meta, "ge"):
                        entry["min"] = meta.ge
                    if hasattr(meta, "le"):
                        entry["max"] = meta.le
                entry["step"] = _STEPS.get(full, 1)
            else:
                entry["type"] = "text"
            out.append(entry)
    return out
