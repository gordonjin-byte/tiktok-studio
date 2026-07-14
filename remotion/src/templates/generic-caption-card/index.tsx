import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

// Universal fallback: must ALWAYS render successfully given just a string.
// No enums, no required colors, nothing that can fail validation.
export const genericCaptionCardSchema = z.object({
  text: z.string().min(1).max(280),
});

export type GenericCaptionCardProps = z.infer<typeof genericCaptionCardSchema>;

export default function GenericCaptionCard(props: GenericCaptionCardProps) {
  const { text } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, width } = useVideoConfig();
  const progress = enterExitProgress(frame, durationInFrames, 10, 10);
  const translateY = interpolate(progress, [0, 1], [24, 0]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: "12%",
      }}
    >
      <div
        style={{
          opacity: progress,
          transform: `translateY(${translateY}px)`,
          maxWidth: width * 0.86,
          padding: "26px 38px",
          borderRadius: 22,
          background: "rgba(10,12,16,0.72)",
          border: "2px solid rgba(255,255,255,0.14)",
          boxShadow: "0 12px 40px rgba(0,0,0,0.45)",
        }}
      >
        <div
          style={{
            fontFamily: FONT_STACK,
            fontSize: 46,
            fontWeight: 700,
            color: "#ffffff",
            textAlign: "center",
            lineHeight: 1.25,
            textShadow: "0 2px 10px rgba(0,0,0,0.5)",
          }}
        >
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
}
