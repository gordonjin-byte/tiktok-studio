import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const lowerThirdLabelSchema = z.object({
  label: z.string().min(1).max(60),
  sublabel: z.string().max(80).optional(),
  accentColor: z.string().default("#3fa9ff"),
  position: z.enum(["top", "bottom"]).default("bottom"),
});

export type LowerThirdLabelProps = z.infer<typeof lowerThirdLabelSchema>;

export default function LowerThirdLabel(props: LowerThirdLabelProps) {
  const { label, sublabel, accentColor, position } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, height } = useVideoConfig();

  const slideP = spring({
    frame,
    fps,
    config: { damping: 16, stiffness: 180, mass: 0.6 },
  });
  const overall = enterExitProgress(frame, durationInFrames, 4, 12);
  const SLIDE_DISTANCE = 500;
  const slideX = interpolate(slideP, [0, 1], [-SLIDE_DISTANCE, 0]);

  const barGrowEnd = Math.max(8, Math.min(16, Math.round(durationInFrames * 0.3)));
  const barGrow = interpolate(frame, [4, barGrowEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: position === "bottom" ? "flex-end" : "flex-start",
      }}
    >
      <div
        style={{
          margin: position === "bottom" ? "0 0 14% 0" : "14% 0 0 0",
          marginLeft: 44,
          opacity: overall,
          transform: `translateX(${slideX}px)`,
          display: "flex",
          alignItems: "stretch",
          maxWidth: "80%",
        }}
      >
        <div
          style={{
            width: 8,
            borderRadius: 4,
            background: accentColor,
            transform: `scaleY(${barGrow})`,
            transformOrigin: "center",
          }}
        />
        <div
          style={{
            marginLeft: 18,
            background: "rgba(8,9,12,0.72)",
            borderRadius: 12,
            padding: "16px 26px",
            borderTop: "1px solid rgba(255,255,255,0.12)",
            borderBottom: "1px solid rgba(255,255,255,0.12)",
          }}
        >
          <div
            style={{
              fontFamily: FONT_STACK,
              fontWeight: 800,
              fontSize: 38,
              color: "#fff",
              lineHeight: 1.1,
            }}
          >
            {label}
          </div>
          {sublabel ? (
            <div
              style={{
                fontFamily: FONT_STACK,
                fontWeight: 500,
                fontSize: 24,
                color: accentColor,
                marginTop: 4,
              }}
            >
              {sublabel}
            </div>
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
}
