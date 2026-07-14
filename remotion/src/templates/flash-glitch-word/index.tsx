import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, random } from "remotion";
import { z } from "zod";
import { FONT_STACK } from "../../lib/anim";

// Full-bleed brief takeover — intentionally opaque (per spec's carve-out for
// "explicit full-screen moments").
export const flashGlitchWordSchema = z.object({
  text: z.string().max(40).default("GLITCH"),
  flashColor: z.string().default("#00ffe0"),
  glitchIntensity: z.enum(["low", "med", "high"]).default("med"),
});

export type FlashGlitchWordProps = z.infer<typeof flashGlitchWordSchema>;

const INTENSITY = {
  low: { shakePx: 4, sliceCount: 3, flashFrames: 3 },
  med: { shakePx: 10, sliceCount: 6, flashFrames: 4 },
  high: { shakePx: 20, sliceCount: 10, flashFrames: 6 },
} as const;

export default function FlashGlitchWord(props: FlashGlitchWordProps) {
  const { text, flashColor, glitchIntensity } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, width, height } = useVideoConfig();
  const cfg = INTENSITY[glitchIntensity];

  const flashOpacity = interpolate(
    frame,
    [0, cfg.flashFrames, cfg.flashFrames + 3],
    [1, 1, 0],
    { extrapolateRight: "clamp" },
  );
  const overallOpacity = interpolate(
    frame,
    [0, 2, durationInFrames - 6, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const slices = Array.from({ length: cfg.sliceCount }, (_, i) => {
    const seed = `slice-${i}-${Math.floor(frame / 2)}`;
    const jitter = (random(seed) - 0.5) * 2 * cfg.shakePx;
    const top = (i / cfg.sliceCount) * 100;
    const h = 100 / cfg.sliceCount;
    return { top, h, jitter };
  });

  const bigShakeX =
    (random(`gx-${Math.floor(frame / 2)}`) - 0.5) * 2 * cfg.shakePx * 0.6;
  const bigShakeY =
    (random(`gy-${Math.floor(frame / 2)}`) - 0.5) * 2 * cfg.shakePx * 0.3;

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      <AbsoluteFill style={{ opacity: overallOpacity }}>
        {slices.map((s, i) => (
          <div
            key={i}
            style={{
              position: "absolute",
              top: `${s.top}%`,
              height: `${s.h}%`,
              width: "100%",
              transform: `translateX(${s.jitter}px)`,
              overflow: "hidden",
            }}
          >
            <AbsoluteFill
              style={{
                top: `-${s.top}%`,
                height: `${(100 / s.h) * 100}%`,
                justifyContent: "center",
                alignItems: "center",
              }}
            >
              <div
                style={{
                  fontFamily: FONT_STACK,
                  fontWeight: 900,
                  fontSize: Math.min(width * 0.16, 150),
                  color: i % 2 === 0 ? flashColor : "#ff00aa",
                  letterSpacing: 2,
                  transform: `translate(${bigShakeX}px, ${bigShakeY}px)`,
                  textShadow: `4px 0 ${flashColor}, -4px 0 #ff00aa`,
                }}
              >
                {text}
              </div>
            </AbsoluteFill>
          </div>
        ))}
      </AbsoluteFill>
      <AbsoluteFill
        style={{
          background: flashColor,
          opacity: flashOpacity * 0.85,
          mixBlendMode: "screen",
        }}
      />
    </AbsoluteFill>
  );
}
