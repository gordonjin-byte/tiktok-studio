"""EDL + settings → .ass subtitle file (captions, banner, CTA, hook overlays).
Style block proven in the manual session; colors converted RGB hex → ASS BGR."""
from __future__ import annotations

from pathlib import Path

from ..models import RenderSettings
from .edl import HOOK_WINDOW_S, TITLE_CARD_S


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """'#RRGGBB' → '&HAABBGGRR' (ASS is BGR with leading alpha)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(t, 0.0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def build_ass(edl: dict, settings: RenderSettings, out_path: Path) -> None:
    s = settings
    c = s.captions
    white = _ass_color(c.color)
    accent = _ass_color(c.highlight_color)
    total = edl["total_out_s"]
    variant = edl["variant"]

    styles = [
        f"Style: Caption,{c.font},{c.size},{white},{white},&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{c.outline_width},3,2,60,60,{c.position_v},1",
        f"Style: Hook,{c.font},{int(c.size * 1.35)},{white},{white},&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{c.outline_width + 2},4,5,80,80,0,1",
        f"Style: TitleCard,{c.font},{int(c.size * 1.15)},{accent},{white},&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,1,0,1,{max(c.outline_width - 2, 3)},3,5,90,90,0,1",
        f"Style: Banner,{c.font},38,&H5CFFFFFF,&H00FFFFFF,&H66000000,&H00000000,"
        f"-1,0,0,0,100,100,3,0,1,3,0,8,60,60,88,1",
        f"Style: QBanner,{c.font},46,&H00000000,&H00FFFFFF,{accent},&H00000000,"
        f"-1,0,0,0,100,100,2,0,3,10,0,8,80,80,120,1",
        f"Style: CTA,{c.font},58,{accent},&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,1,0,1,7,3,2,60,60,360,1",
    ]

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + "\n".join(styles)
        + "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    anim = {
        "pop": r"{\fscx82\fscy82\t(0,70,\fscx100\fscy100)}",
        "fade": r"{\fad(90,0)}",
        "none": "",
    }[c.animation]

    events: list[str] = []
    lead_in = edl["lead_in_s"]
    hook_end = lead_in + HOOK_WINDOW_S

    # banner (suppressed while question banner is up on variant C)
    if s.overlays.banner_enabled and edl["banner_text"]:
        b0 = hook_end if variant == "hook_c" else lead_in
        events.append(f"Dialogue: 0,{_ts(b0)},{_ts(total)},Banner,,0,0,0,,{_esc(edl['banner_text'].upper())}")

    if s.overlays.cta_enabled and edl["cta_start"] is not None and edl["cta_text"]:
        events.append(
            f"Dialogue: 0,{_ts(edl['cta_start'])},{_ts(total)},CTA,,0,0,0,,"
            rf"{{\fscx80\fscy80\t(0,120,\fscx100\fscy100)}}{_esc(edl['cta_text'].upper())}")

    # variant overlays
    hook_text = s.hook.text_override
    if variant == "hook_b":
        text = (hook_text or edl["hook_texts"]["title_card"]).upper()
        events.append(
            f"Dialogue: 1,{_ts(0)},{_ts(TITLE_CARD_S)},TitleCard,,0,0,0,,"
            rf"{{\q0\fad(60,60)\fscx90\fscy90\t(0,{int(TITLE_CARD_S * 1000)},\fscx100\fscy100)}}{_esc(text)}")
    elif variant == "hook_c":
        text = (hook_text or edl["hook_texts"]["question_banner"]).upper()
        events.append(
            f"Dialogue: 1,{_ts(lead_in)},{_ts(hook_end)},QBanner,,0,0,0,,"
            rf"{{\q0\fad(80,120)}}{_esc(text)}")

    # captions
    if c.enabled:
        for i, ch in enumerate(edl["caption_chunks"]):
            words = ch["words"]
            if variant == "hook_c" and ch["t0"] < hook_end and len(words) > 2:
                # force short chunks inside the question-hook window
                mid = (len(words) + 1) // 2
                halves = [words[:mid], words[mid:]]
                dur = ch["t1"] - ch["t0"]
                subs = [
                    {"t0": ch["t0"], "t1": ch["t0"] + dur / 2, "words": halves[0]},
                    {"t0": ch["t0"] + dur / 2, "t1": ch["t1"], "words": halves[1]},
                ]
            else:
                subs = [ch]
            for sub in subs:
                style = "Caption"
                wrap = ""
                if variant == "hook_a" and i == 0 and sub is subs[0]:
                    style = "Hook"
                    wrap = r"{\q0}"
                text = _render_words(sub["words"], c, white, accent)
                events.append(
                    f"Dialogue: 0,{_ts(sub['t0'])},{_ts(sub['t1'])},{style},,0,0,0,,{wrap}{anim}{text}")

    out_path.write_text(header + "\n".join(events) + "\n")


def _render_words(words: list[dict], c, white: str, accent: str) -> str:
    parts = []
    for w in words:
        txt = _esc(w["text"].upper() if c.uppercase else w["text"])
        if w.get("highlight") and c.highlight_keywords:
            parts.append(rf"{{\c{accent}}}{txt}{{\c{white}}}")
        else:
            parts.append(txt)
    return " ".join(parts)


def _esc(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", r"\N")
