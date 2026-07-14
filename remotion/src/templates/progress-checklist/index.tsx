import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress, staggerStep } from "../../lib/anim";

export const progressChecklistSchema = z.object({
  items: z.array(z.string().max(70)).min(1).max(8),
  activeIndex: z.number().int().min(0).default(0),
  completedIndices: z.array(z.number().int().min(0)).default([]),
});

export type ProgressChecklistProps = z.infer<typeof progressChecklistSchema>;

export default function ProgressChecklist(props: ProgressChecklistProps) {
  const { items, activeIndex, completedIndices } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();
  const overall = enterExitProgress(frame, durationInFrames, 8, 12);
  const completedSet = new Set(completedIndices);

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          opacity: overall,
          width: width * 0.84,
          background: "rgba(9,10,14,0.78)",
          borderRadius: 22,
          border: "2px solid rgba(255,255,255,0.1)",
          boxShadow: "0 20px 55px rgba(0,0,0,0.5)",
          padding: "30px 30px",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        {items.map((item, i) => {
          const { delay, rampLen } = staggerStep(i, items.length, durationInFrames, {
            startFrame: Math.max(4, Math.round(durationInFrames * 0.08)),
          });
          const rowP = interpolate(
            frame,
            [delay, delay + rampLen],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          );
          const isCompleted = completedSet.has(i);
          const isActive = i === activeIndex;

          const checkP = isCompleted
            ? spring({
                frame: frame - delay - rampLen * 0.6,
                fps,
                config: { damping: 10, stiffness: 200, mass: 0.5 },
              })
            : 0;

          const pulse = isActive
            ? 0.6 + 0.4 * Math.sin(frame / 5)
            : 1;

          return (
            <div
              key={i}
              style={{
                opacity: rowP,
                transform: `translateX(${(1 - rowP) * 30}px)`,
                display: "flex",
                alignItems: "center",
                gap: 16,
                padding: "10px 14px",
                borderRadius: 12,
                background: isActive
                  ? "rgba(63,169,255,0.14)"
                  : "rgba(255,255,255,0.04)",
                border: isActive
                  ? "1px solid rgba(63,169,255,0.55)"
                  : "1px solid transparent",
                boxShadow: isActive
                  ? `0 0 ${18 * pulse}px rgba(63,169,255,0.35)`
                  : "none",
              }}
            >
              <div
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: "50%",
                  flexShrink: 0,
                  border: `2.5px solid ${
                    isCompleted ? "#33e39a" : isActive ? "#3fa9ff" : "rgba(255,255,255,0.35)"
                  }`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: isCompleted
                    ? "rgba(51,227,154,0.16)"
                    : "transparent",
                }}
              >
                {isCompleted ? (
                  <div
                    style={{
                      transform: `scale(${checkP})`,
                      color: "#33e39a",
                      fontSize: 22,
                      fontWeight: 900,
                    }}
                  >
                    ✓
                  </div>
                ) : isActive ? (
                  <div
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: "50%",
                      background: "#3fa9ff",
                      opacity: pulse,
                    }}
                  />
                ) : null}
              </div>
              <div
                style={{
                  fontFamily: FONT_STACK,
                  fontSize: 27,
                  fontWeight: isActive ? 700 : 500,
                  color: isCompleted
                    ? "rgba(255,255,255,0.55)"
                    : "#fff",
                  textDecoration: isCompleted ? "line-through" : "none",
                }}
              >
                {item}
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
}
