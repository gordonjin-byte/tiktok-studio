import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const warningBannerSchema = z.object({
  text: z.string().min(1).max(100),
  severity: z.enum(["info", "warning", "danger"]).default("warning"),
  icon: z.string().max(8).default("⚠️"),
  pulsing: z.boolean().default(true),
});

export type WarningBannerProps = z.infer<typeof warningBannerSchema>;

const SEVERITY_COLOR: Record<string, string> = {
  info: "#3fa9ff",
  warning: "#ffb020",
  danger: "#ff3355",
};

export default function WarningBanner(props: WarningBannerProps) {
  const { text, severity, icon, pulsing } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();
  const overall = enterExitProgress(frame, durationInFrames, 8, 10);
  const color = SEVERITY_COLOR[severity];

  const slideP = spring({
    frame,
    fps,
    config: { damping: 15, stiffness: 200, mass: 0.6 },
  });
  const slideY = interpolate(slideP, [0, 1], [-60, 0]);

  const pulse = pulsing ? 0.5 + 0.5 * Math.sin(frame / 6) : 1;
  const glowAlpha = pulsing ? 0.25 + 0.25 * pulse : 0.25;

  return (
    <AbsoluteFill style={{ justifyContent: "flex-start" }}>
      <div
        style={{
          marginTop: "8%",
          alignSelf: "center",
          opacity: overall,
          transform: `translateY(${slideY}px)`,
          width: width * 0.9,
          display: "flex",
          alignItems: "center",
          gap: 16,
          background: "rgba(10,11,14,0.82)",
          border: `2px solid ${color}`,
          borderRadius: 16,
          padding: "20px 26px",
          boxShadow: `0 0 ${28 * (pulsing ? 0.6 + 0.4 * pulse : 1)}px ${color}${Math.round(
            glowAlpha * 255,
          )
            .toString(16)
            .padStart(2, "0")}`,
        }}
      >
        <div style={{ fontSize: 44, flexShrink: 0 }}>{icon}</div>
        <div
          style={{
            fontFamily: FONT_STACK,
            fontWeight: 700,
            fontSize: 32,
            color: "#fff",
            lineHeight: 1.25,
          }}
        >
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
}
