"""EDL + settings → ffmpeg filter_complex script.

Graph shape (proven in the manual session):
- video: per-video-segment trim → scale(lanczos, zoom) → crop 1080x1920 → concat
         (+ variant B: 0.8s darkened first-frame hold prepended)
- audio: per-AUDIO-segment atrim (continuous across video-only zoom cuts) → afade
         in/out at splices → concat → [+ sfx amix] → [+ ducked music amix] → loudnorm
- grade: eq/hue preset after concat; ass= burn happens in render.py's -vf
  (subtitles need the final composited frame; keeping it in -vf keeps this graph pure)
"""
from __future__ import annotations

from pathlib import Path

from .. import config
from ..models import RenderSettings
from .edl import TITLE_CARD_S

_GRADE = {
    "none": None,
    "punchy": "eq=contrast=1.06:saturation=1.15",
    "warm": "eq=contrast=1.04:saturation=1.1,colorbalance=rm=0.05:bm=-0.05",
    "cool": "eq=contrast=1.04:saturation=1.05,colorbalance=bm=0.06:rm=-0.03",
    "bw": "hue=s=0,eq=contrast=1.12",
}

_DUCK_RATIO = {"light": 4, "medium": 8, "heavy": 14}


def build_filter_script(edl: dict, settings: RenderSettings, out_path: Path,
                        music_path: Path | None, sfx_whoosh: Path | None,
                        sfx_pop: Path | None, ass_path: Path | None = None,
                        overlay_clips: list[dict] | None = None) -> dict:
    """Writes the filter_complex script; returns {'inputs': [...extra input paths],
    'v_label': ..., 'a_label': ...} for render.py to assemble the command.
    The ass burn lives inside the graph (-vf can't combine with -filter_complex).

    overlay_clips: [{"cue_id","start_out","end_out","path": Path, "z_index": int}]
    — already-resolved existing alpha clip paths (script-driven Remotion
    overlays); render.py drops missing/failed cues before calling this."""
    s = settings
    W, H = config.OUT_W, config.OUT_H
    parts: list[str] = []
    total = edl["total_out_s"]
    inputs: list[str] = []
    next_input = 1

    # ---------- video ----------
    vlabels: list[str] = []
    n_v = 0
    if edl["variant"] == "hook_b":
        first_src = edl["video_segments"][0]["src0"]
        n_card_frames = int(TITLE_CARD_S * config.FPS)
        parts.append(
            f"[0:v]trim={first_src}:{first_src + 0.2},setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:flags=lanczos,crop={W}:{H},"
            f"loop=loop={n_card_frames}:size=1:start=0,"
            f"trim=0:{TITLE_CARD_S},setpts=N/{config.FPS}/TB,"
            f"eq=brightness=-0.28:saturation=0.6[vcard]")
        vlabels.append("[vcard]")
        n_v += 1
    for seg in edl["video_segments"]:
        z = seg["zoom"]
        zw, zh = int(W * z / 2) * 2, int(H * z / 2) * 2
        parts.append(
            f"[0:v]trim={seg['src0']}:{seg['src1']},setpts=PTS-STARTPTS,"
            f"scale={zw}:{zh}:flags=lanczos,crop={W}:{H}[v{n_v}]")
        vlabels.append(f"[v{n_v}]")
        n_v += 1
    parts.append("".join(vlabels) + f"concat=n={n_v}:v=1:a=0[vcat]")

    vlab = "[vcat]"
    grade = _GRADE[s.color.preset]
    if grade:
        parts.append(f"{vlab}{grade}[vgrade]")
        vlab = "[vgrade]"

    # ---------- script-driven overlays (after grade, before captions so
    # captions always stay the topmost/readable layer) ----------
    if overlay_clips:
        for j, clip in enumerate(sorted(overlay_clips, key=lambda c: (c.get("z_index", 0), c["start_out"]))):
            idx = next_input
            inputs.append(str(clip["path"]))
            next_input += 1
            olab = f"[vovl{j}]"
            parts.append(
                f"{vlab}[{idx}:v]overlay=0:0:enable='between(t,{clip['start_out']},{clip['end_out']})'{olab}")
            vlab = olab

    if ass_path is not None:
        esc = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        parts.append(f"{vlab}ass='{esc}'[vsub]")
        vlab = "[vsub]"

    # ---------- audio: voice ----------
    fade = s.cuts.crossfade_ms / 1000.0
    alabels: list[str] = []
    if edl["variant"] == "hook_b":
        parts.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{TITLE_CARD_S},asetpts=PTS-STARTPTS[acard]")
        alabels.append("[acard]")
    for i, seg in enumerate(edl["audio_segments"]):
        d = seg["src1"] - seg["src0"]
        parts.append(
            f"[0:a]atrim={seg['src0']}:{seg['src1']},asetpts=PTS-STARTPTS,"
            f"aresample=44100,aformat=channel_layouts=stereo,"
            f"afade=t=in:d={fade},afade=t=out:st={max(d - fade, 0)}:d={fade}[a{i}]")
        alabels.append(f"[a{i}]")
    parts.append("".join(alabels) + f"concat=n={len(alabels)}:v=0:a=1[voice]")
    alab = "[voice]"

    # ---------- sfx ----------
    sfx_events: list[tuple[Path, float]] = []
    if s.sfx.whoosh_on_zoom and sfx_whoosh and sfx_whoosh.exists():
        sfx_events.extend((sfx_whoosh, t) for t in edl["cut_times_out"][:40])
        if edl["variant"] == "hook_b":
            sfx_events.append((sfx_whoosh, max(TITLE_CARD_S - 0.15, 0)))
    if s.sfx.pop_on_hook and sfx_pop and sfx_pop.exists() and edl["caption_chunks"]:
        sfx_events.append((sfx_pop, edl["caption_chunks"][0]["t0"]))
    if sfx_events:
        gain = 10 ** (s.sfx.volume_db / 20)
        sfx_labels = []
        for j, (path, t) in enumerate(sfx_events):
            idx = next_input
            inputs.append(str(path))
            next_input += 1
            delay_ms = max(int(t * 1000), 0)
            parts.append(
                f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
                f"volume={gain:.4f},adelay={delay_ms}|{delay_ms}[sfx{j}]")
            sfx_labels.append(f"[sfx{j}]")
        parts.append(
            alab + "".join(sfx_labels) +
            f"amix=inputs={len(sfx_labels) + 1}:duration=first:normalize=0[vsfx]")
        alab = "[vsfx]"

    # ---------- music ----------
    if s.music.track and music_path and music_path.exists():
        idx = next_input
        inputs.append(str(music_path))
        next_input += 1
        gain = 10 ** (s.music.volume_db / 20)
        parts.append(
            f"[{idx}:a]aloop=loop=-1:size=2e9,atrim=0:{total},asetpts=PTS-STARTPTS,"
            f"aresample=44100,aformat=channel_layouts=stereo,volume={gain:.4f}[mus]")
        mlab = "[mus]"
        if s.music.ducking:
            ratio = _DUCK_RATIO[s.music.duck_amount]
            parts.append(f"{alab}asplit=2[voice_mix][voice_key]")
            parts.append(
                f"{mlab}[voice_key]sidechaincompress="
                f"threshold=0.02:ratio={ratio}:attack=40:release=400[musduck]")
            parts.append(f"[voice_mix][musduck]amix=inputs=2:duration=first:normalize=0[amixed]")
        else:
            parts.append(f"{alab}{mlab}amix=inputs=2:duration=first:normalize=0[amixed]")
        alab = "[amixed]"

    # ---------- loudness last ----------
    parts.append(
        f"{alab}loudnorm=I={s.audio.loudness_lufs}:TP={s.audio.true_peak_db}:LRA=11[aout]")

    out_path.write_text(";\n".join(parts) + "\n")
    return {"inputs": inputs, "v_label": vlab, "a_label": "[aout]"}
