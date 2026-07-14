import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress, staggerStep } from "../../lib/anim";

export const splitScreenCompareSchema = z.object({
  leftLabel: z.string().min(1).max(40),
  leftIcon: z.string().max(8).default("❌"),
  leftStatus: z.array(z.string().max(60)).min(1).max(5),
  rightLabel: z.string().min(1).max(40),
  rightIcon: z.string().max(8).default("✅"),
  rightStatus: z.array(z.string().max(60)).min(1).max(5),
});

export type SplitScreenCompareProps = z.infer<typeof splitScreenCompareSchema>;

function Column({
  side,
  label,
  icon,
  status,
  frame,
  fps,
  durationInFrames,
  accent,
}: {
  side: "left" | "right";
  label: string;
  icon: string;
  status: string[];
  frame: number;
  fps: number;
  durationInFrames: number;
  accent: string;
}) {
  const slideFrom = side === "left" ? -80 : 80;
  const enter = interpolate(frame, [0, 14], [1, 0], {
    extrapolateRight: "clamp",
  });
  const slide = enter * slideFrom;
  const overall = enterExitProgress(frame, durationInFrames, 4, 10);

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 16,
        padding: "0 22px",
        opacity: overall,
        transform: `translateX(${slide}px)`,
      }}
    >
      <div style={{ fontSize: 64 }}>{icon}</div>
      <div
        style={{
          fontFamily: FONT_STACK,
          fontWeight: 800,
          fontSize: 34,
          color: accent,
          textAlign: "center",
        }}
      >
        {label}
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 10,
          width: "100%",
        }}
      >
        {status.map((s, i) => {
          const { delay, rampLen } = staggerStep(i, status.length, durationInFrames, {
            startFrame: Math.max(10, Math.round(durationInFrames * 0.18)),
          });
          const localP = interpolate(frame, [delay, delay + rampLen], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          return (
            <div
              key={i}
              style={{
                opacity: localP,
                transform: `translateY(${(1 - localP) * 10}px)`,
                fontFamily: FONT_STACK,
                fontSize: 24,
                fontWeight: 500,
                color: "rgba(255,255,255,0.92)",
                background: "rgba(255,255,255,0.06)",
                borderRadius: 10,
                padding: "9px 14px",
                textAlign: "center",
              }}
            >
              {s}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function SplitScreenCompare(props: SplitScreenCompareProps) {
  const {
    leftLabel,
    leftIcon,
    leftStatus,
    rightLabel,
    rightIcon,
    rightStatus,
  } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, height } = useVideoConfig();
  const dividerScale = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          width: "94%",
          borderRadius: 26,
          background: "rgba(8,9,12,0.78)",
          border: "2px solid rgba(255,255,255,0.1)",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
          display: "flex",
          padding: "40px 10px",
          position: "relative",
        }}
      >
        <Column
          side="left"
          label={leftLabel}
          icon={leftIcon}
          status={leftStatus}
          frame={frame}
          fps={fps}
          durationInFrames={durationInFrames}
          accent="#ff5f6d"
        />
        <div
          style={{
            width: 2,
            background: "rgba(255,255,255,0.18)",
            transform: `scaleY(${dividerScale})`,
            transformOrigin: "center",
          }}
        />
        <Column
          side="right"
          label={rightLabel}
          icon={rightIcon}
          status={rightStatus}
          frame={frame}
          fps={fps}
          durationInFrames={durationInFrames}
          accent="#33e39a"
        />
      </div>
    </AbsoluteFill>
  );
}
