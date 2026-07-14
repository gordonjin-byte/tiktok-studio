import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const beforeAfterToggleSchema = z
  .object({
    beforeLabel: z.string().min(1).max(40),
    beforeIcon: z.string().max(8).optional(),
    beforeText: z.string().max(60).optional(),
    afterLabel: z.string().min(1).max(40),
    afterIcon: z.string().max(8).optional(),
    afterText: z.string().max(60).optional(),
    toggleStyle: z.enum(["wipe", "crossfade", "flip"]).default("crossfade"),
  })
  .refine((v) => v.beforeIcon || v.beforeText, {
    message: "either beforeIcon or beforeText must be provided",
  })
  .refine((v) => v.afterIcon || v.afterText, {
    message: "either afterIcon or afterText must be provided",
  });

export type BeforeAfterToggleProps = z.infer<typeof beforeAfterToggleSchema>;

function Face({
  label,
  icon,
  text,
  accent,
}: {
  label: string;
  icon?: string;
  text?: string;
  accent: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 14,
      }}
    >
      <div
        style={{
          fontFamily: FONT_STACK,
          fontWeight: 800,
          fontSize: 26,
          color: accent,
          letterSpacing: 2,
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      {icon ? (
        <div style={{ fontSize: 88 }}>{icon}</div>
      ) : (
        <div
          style={{
            fontFamily: FONT_STACK,
            fontWeight: 700,
            fontSize: 40,
            color: "#fff",
            textAlign: "center",
          }}
        >
          {text}
        </div>
      )}
    </div>
  );
}

export default function BeforeAfterToggle(props: BeforeAfterToggleProps) {
  const {
    beforeLabel,
    beforeIcon,
    beforeText,
    afterLabel,
    afterIcon,
    afterText,
    toggleStyle,
  } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, width } = useVideoConfig();
  const overall = enterExitProgress(frame, durationInFrames, 8, 12);

  // Toggle happens at the midpoint of the clip, with a short transition window.
  const mid = durationInFrames / 2;
  const transWindow = 10;
  const t = interpolate(
    frame,
    [mid - transWindow / 2, mid + transWindow / 2],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  let beforeStyle: React.CSSProperties = {};
  let afterStyle: React.CSSProperties = {};

  if (toggleStyle === "crossfade") {
    beforeStyle = { opacity: 1 - t, position: "absolute" };
    afterStyle = { opacity: t, position: "absolute" };
  } else if (toggleStyle === "wipe") {
    beforeStyle = {
      position: "absolute",
      clipPath: `inset(0 ${t * 100}% 0 0)`,
    };
    afterStyle = {
      position: "absolute",
      clipPath: `inset(0 0 0 ${(1 - t) * 100}%)`,
    };
  } else {
    // flip
    const rotate = t * 180;
    beforeStyle = {
      position: "absolute",
      opacity: t < 0.5 ? 1 : 0,
      transform: `rotateY(${rotate}deg)`,
      backfaceVisibility: "hidden",
    };
    afterStyle = {
      position: "absolute",
      opacity: t >= 0.5 ? 1 : 0,
      transform: `rotateY(${rotate - 180}deg)`,
      backfaceVisibility: "hidden",
    };
  }

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          opacity: overall,
          width: width * 0.72,
          height: 340,
          borderRadius: 24,
          background: "rgba(9,10,14,0.8)",
          border: "2px solid rgba(255,255,255,0.12)",
          boxShadow: "0 20px 55px rgba(0,0,0,0.5)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          position: "relative",
          perspective: 1200,
        }}
      >
        <div style={beforeStyle}>
          <Face
            label={beforeLabel}
            icon={beforeIcon}
            text={beforeText}
            accent="#ff5f6d"
          />
        </div>
        <div style={afterStyle}>
          <Face
            label={afterLabel}
            icon={afterIcon}
            text={afterText}
            accent="#33e39a"
          />
        </div>
      </div>
    </AbsoluteFill>
  );
}
