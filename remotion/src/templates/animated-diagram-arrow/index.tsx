import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const animatedDiagramArrowSchema = z.object({
  fromLabel: z.string().min(1).max(40),
  toLabel: z.string().min(1).max(40),
  arrowStyle: z.enum(["straight", "curved", "dashed"]).default("straight"),
  direction: z.enum(["ltr", "rtl", "up", "down"]).default("ltr"),
});

export type AnimatedDiagramArrowProps = z.infer<
  typeof animatedDiagramArrowSchema
>;

const NODE_W = 320;
const NODE_H = 140;

export default function AnimatedDiagramArrow(
  props: AnimatedDiagramArrowProps,
) {
  const { fromLabel, toLabel, arrowStyle, direction } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, width, height } = useVideoConfig();
  // Three internal stages (nodeA in -> arrow draws -> nodeB in) must all
  // complete, with a moment to hold, before the overall envelope's own
  // fade-out begins — derive every stage boundary from durationInFrames
  // (with floors) instead of fixed absolute frame numbers, so this still
  // reads correctly on a short ~20-24 frame (~0.7-0.8s) clip.
  const outFrames = Math.max(4, Math.min(12, Math.round(durationInFrames * 0.22)));
  const inFrames = Math.max(4, Math.min(8, Math.round(durationInFrames * 0.15)));
  const overall = enterExitProgress(frame, durationInFrames, inFrames, outFrames);

  const horizontal = direction === "ltr" || direction === "rtl";
  const reversed = direction === "rtl" || direction === "up";

  const nodeAEnd = Math.max(6, Math.round(durationInFrames * 0.18));
  const nodeAOpacity = interpolate(frame, [0, nodeAEnd], [0, 1], {
    extrapolateRight: "clamp",
  });

  const drawStart = Math.max(4, Math.round(nodeAEnd * 0.75));
  const drawEnd = Math.max(drawStart + 6, Math.round(durationInFrames * 0.55));
  const drawProgress = interpolate(frame, [drawStart, drawEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const nodeBDelay = Math.max(drawEnd - 3, Math.round(durationInFrames * 0.5));
  const nodeBRamp = Math.max(4, Math.min(10, Math.round(durationInFrames * 0.2)));
  const nodeBOpacity = interpolate(
    frame,
    [nodeBDelay, nodeBDelay + nodeBRamp],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const gap = 220;
  const svgW = horizontal ? gap : 4;
  const svgH = horizontal ? 4 : gap;

  const pathLen = horizontal ? gap : gap;
  const dashLen = arrowStyle === "dashed" ? 18 : pathLen;
  const dashGap = arrowStyle === "dashed" ? 14 : 0;

  const pathD = horizontal
    ? arrowStyle === "curved"
      ? `M0,${svgH / 2} Q${gap / 2},${svgH / 2 - 60} ${gap},${svgH / 2}`
      : `M0,${svgH / 2} L${gap},${svgH / 2}`
    : arrowStyle === "curved"
      ? `M${svgW / 2},0 Q${svgW / 2 + 60},${gap / 2} ${svgW / 2},${gap}`
      : `M${svgW / 2},0 L${svgW / 2},${gap}`;

  const arrowHeadRotation = horizontal
    ? reversed
      ? 180
      : 0
    : reversed
      ? 0
      : 90;

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          opacity: overall,
          display: "flex",
          flexDirection: horizontal ? "row" : "column",
          alignItems: "center",
          gap: 0,
        }}
      >
        <Node
          label={reversed ? toLabel : fromLabel}
          opacity={nodeAOpacity}
          color="#3fa9ff"
        />
        <div
          style={{
            width: horizontal ? gap : NODE_W * 0.6,
            height: horizontal ? 60 : gap,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            position: "relative",
          }}
        >
          <svg
            width={horizontal ? gap : 60}
            height={horizontal ? 60 : gap}
            style={{ overflow: "visible" }}
          >
            <path
              d={pathD}
              stroke="#ffd400"
              strokeWidth={6}
              fill="none"
              strokeLinecap="round"
              strokeDasharray={
                arrowStyle === "dashed" ? `${dashLen} ${dashGap}` : pathLen
              }
              strokeDashoffset={pathLen * (1 - drawProgress)}
            />
          </svg>
          <div
            style={{
              position: "absolute",
              opacity: drawProgress > 0.9 ? 1 : 0,
              transform: `rotate(${arrowHeadRotation}deg) ${
                horizontal ? "translateX(100px)" : "translateY(100px)"
              }`,
              fontSize: 34,
              color: "#ffd400",
            }}
          >
            ➤
          </div>
        </div>
        <Node
          label={reversed ? fromLabel : toLabel}
          opacity={nodeBOpacity}
          color="#33e39a"
        />
      </div>
    </AbsoluteFill>
  );
}

function Node({
  label,
  opacity,
  color,
}: {
  label: string;
  opacity: number;
  color: string;
}) {
  return (
    <div
      style={{
        opacity,
        width: NODE_W,
        height: NODE_H,
        borderRadius: 18,
        background: "rgba(10,12,16,0.8)",
        border: `2px solid ${color}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "0 18px",
        boxShadow: `0 12px 30px rgba(0,0,0,0.4)`,
      }}
    >
      <div
        style={{
          fontFamily: FONT_STACK,
          fontWeight: 700,
          fontSize: 30,
          color: "#fff",
          textAlign: "center",
        }}
      >
        {label}
      </div>
    </div>
  );
}
