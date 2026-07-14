import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

// Full-bleed takeover — intentionally opaque (explicit exception in spec:
// a brief full-screen "quote moment" is the intended effect here).
export const quoteCardSchema = z.object({
  text: z.string().min(1).max(220),
  attribution: z.string().max(60).optional(),
  style: z.enum(["bold", "minimal"]).default("bold"),
  bgColor: z.string().default("#0d0f12"),
  textColor: z.string().default("#ffffff"),
});

export type QuoteCardProps = z.infer<typeof quoteCardSchema>;

export default function QuoteCard(props: QuoteCardProps) {
  const { text, attribution, style, bgColor, textColor } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();
  const overall = enterExitProgress(frame, durationInFrames, 14, 16);

  const textP = spring({
    frame,
    fps,
    config: { damping: 16, stiffness: 120, mass: 0.7 },
  });
  const textScale = interpolate(textP, [0, 1], [0.85, 1]);

  // Attribution fades in after the main quote text, but must finish before
  // the overall envelope's own fade-out (14/16-frame ramps, guarded by
  // enterExitProgress's internal clamp) — scale its window to duration.
  const attrStart = Math.max(8, Math.round(durationInFrames * 0.22));
  const attrRamp = Math.max(4, Math.min(12, Math.round(durationInFrames * 0.15)));
  const attrP = interpolate(frame, [attrStart, attrStart + attrRamp], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const bgFade = interpolate(frame, [0, Math.min(8, Math.max(3, Math.round(durationInFrames * 0.25)))], [0, 1], {
    extrapolateRight: "clamp",
  });

  const isBold = style === "bold";

  return (
    <AbsoluteFill
      style={{
        background: bgColor,
        opacity: bgFade,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <div
        style={{
          opacity: overall,
          transform: `scale(${textScale})`,
          width: width * 0.82,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 26,
        }}
      >
        {isBold ? (
          <div
            style={{
              fontFamily: FONT_STACK,
              fontSize: 96,
              lineHeight: 1,
              color: textColor,
              opacity: 0.35,
            }}
          >
            "
          </div>
        ) : null}
        <div
          style={{
            fontFamily: FONT_STACK,
            fontWeight: isBold ? 800 : 500,
            fontSize: isBold ? 54 : 42,
            color: textColor,
            textAlign: "center",
            lineHeight: 1.3,
            fontStyle: isBold ? "normal" : "italic",
          }}
        >
          {text}
        </div>
        {attribution ? (
          <div
            style={{
              fontFamily: FONT_STACK,
              fontWeight: 600,
              fontSize: 28,
              color: textColor,
              opacity: attrP * 0.7,
            }}
          >
            — {attribution}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
}
