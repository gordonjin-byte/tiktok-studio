import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const counterStatTickupSchema = z.object({
  fromValue: z.number().default(0),
  toValue: z.number(),
  prefix: z.string().max(10).optional(),
  suffix: z.string().max(10).optional(),
  label: z.string().max(60),
});

export type CounterStatTickupProps = z.infer<typeof counterStatTickupSchema>;

function easeOutCubic(t: number) {
  return 1 - Math.pow(1 - t, 3);
}

function formatValue(v: number) {
  const rounded = Math.round(v * 10) / 10;
  return Number.isInteger(rounded) ? rounded.toString() : rounded.toFixed(1);
}

export default function CounterStatTickup(props: CounterStatTickupProps) {
  const { fromValue, toValue, prefix, suffix, label } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, width } = useVideoConfig();
  const overall = enterExitProgress(frame, durationInFrames, 10, 12);

  // Tick-up runs over the middle 60% of the clip so it has room to settle
  // before exit fades out.
  const tickEnd = Math.round(durationInFrames * 0.7);
  const rawT = interpolate(frame, [4, tickEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const eased = easeOutCubic(rawT);
  const current = interpolate(eased, [0, 1], [fromValue, toValue]);

  const settled = rawT >= 1;
  const punch = settled
    ? 1 + Math.max(0, 1 - (frame - tickEnd) / 6) * 0.08
    : 1;

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          opacity: overall,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          width: width * 0.8,
          background: "rgba(9,10,14,0.7)",
          borderRadius: 26,
          padding: "44px 30px",
          border: "2px solid rgba(255,255,255,0.1)",
          boxShadow: "0 20px 55px rgba(0,0,0,0.5)",
        }}
      >
        <div
          style={{
            fontFamily: FONT_STACK,
            fontWeight: 900,
            fontSize: 96,
            color: "#ffd400",
            transform: `scale(${punch})`,
            lineHeight: 1,
            textShadow: "0 4px 20px rgba(255,212,0,0.35)",
          }}
        >
          {prefix ?? ""}
          {formatValue(current)}
          {suffix ?? ""}
        </div>
        <div
          style={{
            fontFamily: FONT_STACK,
            fontWeight: 600,
            fontSize: 30,
            color: "rgba(255,255,255,0.85)",
            textAlign: "center",
          }}
        >
          {label}
        </div>
      </div>
    </AbsoluteFill>
  );
}
