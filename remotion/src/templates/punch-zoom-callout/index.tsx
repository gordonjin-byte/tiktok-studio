import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, springIn, springOut } from "../../lib/anim";

export const punchZoomCalloutSchema = z.object({
  punchScale: z.number().min(1).max(3).default(1.4),
  triggerLabel: z.string().max(60).optional(),
  shake: z.boolean().default(true),
  accentColor: z.string().default("#ff3355"),
});

export type PunchZoomCalloutProps = z.infer<typeof punchZoomCalloutSchema>;

export default function PunchZoomCallout(props: PunchZoomCalloutProps) {
  const { punchScale, triggerLabel, shake, accentColor } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();

  const outFrames = Math.max(4, Math.min(12, Math.round(durationInFrames * 0.3)));
  const inP = springIn(frame, fps, { damping: 9, stiffness: 220, mass: 0.5 });
  const outP = springOut(frame, durationInFrames, fps, outFrames);
  const scale = interpolate(inP, [0, 1], [0.4, punchScale]) * (1 - outP * 0.3);
  const opacity = Math.min(inP, 1 - outP);

  const shakeX = shake
    ? Math.sin(frame * 3.1) * 6 * Math.max(0, 1 - frame / 10)
    : 0;
  const shakeY = shake
    ? Math.cos(frame * 2.7) * 5 * Math.max(0, 1 - frame / 10)
    : 0;

  // Ring-burst + full-frame flash tint: a bold, unmissable "impact" moment
  // in the first few frames, not just a small persistent glowing dot. Scale
  // the burst window down for very short clips so it never overruns.
  const burstFrames = Math.max(6, Math.min(14, Math.round(durationInFrames * 0.45)));
  const ringScale = interpolate(
    frame,
    [0, burstFrames],
    [0.2, 3.4],
    { extrapolateRight: "clamp" },
  );
  const ringOpacity = interpolate(
    frame,
    [0, Math.max(1, Math.round(burstFrames * 0.2)), burstFrames],
    [0, 0.8, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const flashOpacity = interpolate(
    frame,
    [0, 2, Math.max(3, Math.round(burstFrames * 0.35))],
    [0, 0.55, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <AbsoluteFill
        style={{ background: accentColor, opacity: flashOpacity, mixBlendMode: "screen" }}
      />
      <div
        style={{
          position: "absolute",
          width: 340,
          height: 340,
          borderRadius: "50%",
          border: `10px solid ${accentColor}`,
          transform: `scale(${ringScale})`,
          opacity: ringOpacity,
          boxShadow: `0 0 80px ${accentColor}`,
        }}
      />
      <div
        style={{
          transform: `translate(${shakeX}px, ${shakeY}px) scale(${scale})`,
          opacity,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
        }}
      >
        <div
          style={{
            width: 260,
            height: 260,
            borderRadius: "50%",
            background: `radial-gradient(circle at 35% 30%, ${accentColor}, #1a0006)`,
            boxShadow: `0 0 90px ${accentColor}cc, 0 0 160px ${accentColor}66`,
          }}
        />
        {triggerLabel ? (
          <div
            style={{
              fontFamily: FONT_STACK,
              fontWeight: 900,
              fontSize: 44,
              color: "#fff",
              background: accentColor,
              padding: "10px 26px",
              borderRadius: 12,
              maxWidth: width * 0.8,
              textAlign: "center",
              boxShadow: "0 10px 30px rgba(0,0,0,0.4)",
            }}
          >
            {triggerLabel}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
}
