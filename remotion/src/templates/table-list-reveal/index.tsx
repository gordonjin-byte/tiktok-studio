import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress, staggerStep } from "../../lib/anim";

export const tableListRevealSchema = z
  .object({
    title: z.string().max(60).optional(),
    items: z.array(z.string().max(80)).max(8).optional(),
    rows: z.array(z.string().max(80)).max(8).optional(),
    revealStyle: z.enum(["stagger", "typewriter"]).default("stagger"),
    highlightIndex: z.number().int().min(0).optional(),
  })
  .refine((v) => (v.items && v.items.length) || (v.rows && v.rows.length), {
    message: "either items or rows must be provided",
  });

export type TableListRevealProps = z.infer<typeof tableListRevealSchema>;

// Plain helper (not a React hook despite the domain) — pure computation, safe
// to call conditionally/inside loops.
function typewriterSlice(text: string, frame: number, startFrame: number, charsPerFrame = 0.9) {
  const chars = Math.max(0, Math.floor((frame - startFrame) * charsPerFrame));
  return text.slice(0, chars);
}

export default function TableListReveal(props: TableListRevealProps) {
  const { title, revealStyle, highlightIndex } = props;
  const list = (props.items && props.items.length ? props.items : props.rows) ?? [];
  const itemCount = Math.max(1, list.length);
  const frame = useCurrentFrame();
  const { durationInFrames, width } = useVideoConfig();
  const overall = enterExitProgress(frame, durationInFrames, 8, 12);

  const titleStart = 0;
  // Title window scales with duration (a fixed 16-frame window can overrun a
  // short clip), and typewriter speed scales with the title's own length so
  // it always finishes within its window regardless of text length.
  const titleEnd = title
    ? Math.max(6, Math.min(16, Math.round(durationInFrames * 0.16)))
    : 0;
  const titleCharsPerFrame = title
    ? Math.max(0.7, title.length / Math.max(1, titleEnd))
    : 1.2;
  const shownTitle =
    title && revealStyle === "typewriter"
      ? typewriterSlice(title, frame, titleStart, titleCharsPerFrame)
      : title;
  const titleOpacity = interpolate(frame, [0, Math.max(2, Math.min(8, titleEnd))], [0, 1], {
    extrapolateRight: "clamp",
  });

  const listStartFrame = title ? titleEnd : Math.max(2, Math.round(durationInFrames * 0.06));

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          opacity: overall,
          width: width * 0.86,
          background: "rgba(9,10,14,0.78)",
          borderRadius: 22,
          border: "2px solid rgba(255,255,255,0.1)",
          boxShadow: "0 20px 55px rgba(0,0,0,0.5)",
          padding: "32px 34px",
        }}
      >
        {title ? (
          <div
            style={{
              fontFamily: FONT_STACK,
              fontWeight: 800,
              fontSize: 36,
              color: "#ffd400",
              opacity: titleOpacity,
              marginBottom: 20,
              minHeight: 44,
            }}
          >
            {shownTitle}
          </div>
        ) : null}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {list.map((item, i) => {
            let itemOpacity: number;
            let itemX: number;
            let shownItem = item;
            if (revealStyle === "typewriter") {
              const { delay: startFrame, rampLen } = staggerStep(i, itemCount, durationInFrames, {
                startFrame: listStartFrame,
                minRamp: 6,
                maxRamp: 20,
              });
              const charsPerFrame = Math.max(0.6, item.length / Math.max(1, rampLen));
              shownItem = typewriterSlice(item, frame, startFrame, charsPerFrame);
              itemOpacity = interpolate(
                frame,
                [startFrame, startFrame + Math.min(3, rampLen)],
                [0, 1],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
              );
              itemX = 0;
            } else {
              const { delay: startFrame, rampLen } = staggerStep(i, itemCount, durationInFrames, {
                startFrame: listStartFrame,
              });
              const p = interpolate(
                frame,
                [startFrame, startFrame + rampLen],
                [0, 1],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
              );
              itemOpacity = p;
              itemX = (1 - p) * 40;
            }
            const isHighlighted = highlightIndex === i;
            return (
              <div
                key={i}
                style={{
                  opacity: itemOpacity,
                  transform: `translateX(${itemX}px)`,
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "10px 16px",
                  borderRadius: 12,
                  background: isHighlighted
                    ? "rgba(255,212,0,0.14)"
                    : "rgba(255,255,255,0.05)",
                  border: isHighlighted
                    ? "1px solid rgba(255,212,0,0.5)"
                    : "1px solid transparent",
                }}
              >
                <div
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: isHighlighted ? "#ffd400" : "#3fa9ff",
                    flexShrink: 0,
                  }}
                />
                <div
                  style={{
                    fontFamily: FONT_STACK,
                    fontSize: 28,
                    fontWeight: 600,
                    color: "#fff",
                    minHeight: 34,
                  }}
                >
                  {shownItem}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
}
