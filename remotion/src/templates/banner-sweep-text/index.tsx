import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const bannerSweepTextSchema = z.object({
  text: z.string().min(1).max(80),
  subtext: z.string().max(120).optional(),
  sweepDirection: z.enum(["left", "right", "top", "bottom"]).default("left"),
  bgColor: z.string().default("#111318"),
  textColor: z.string().default("#ffffff"),
  icon: z.string().max(8).optional(),
});

export type BannerSweepTextProps = z.infer<typeof bannerSweepTextSchema>;

export default function BannerSweepText(props: BannerSweepTextProps) {
  const { text, subtext, sweepDirection, bgColor, textColor, icon } = props;
  const frame = useCurrentFrame();
  const { durationInFrames, width, height } = useVideoConfig();
  const progress = enterExitProgress(frame, durationInFrames, 14, 14);

  const axis =
    sweepDirection === "left" || sweepDirection === "right" ? "X" : "Y";
  const sign =
    sweepDirection === "left" || sweepDirection === "top" ? -1 : 1;
  const distance = axis === "X" ? width * 0.7 : height * 0.4;
  const offset = interpolate(progress, [0, 1], [sign * distance, 0]);

  return (
    <AbsoluteFill style={{ justifyContent: "flex-start" }}>
      <div
        style={{
          marginTop: height * 0.1,
          transform: `translate${axis}(${offset}px)`,
          opacity: interpolate(progress, [0, 0.3, 1], [0, 1, 1]),
          alignSelf: "center",
          maxWidth: width * 0.92,
          background: bgColor,
          borderRadius: 18,
          padding: "30px 44px",
          boxShadow: "0 16px 46px rgba(0,0,0,0.5)",
          border: "2px solid rgba(255,255,255,0.08)",
          display: "flex",
          alignItems: "center",
          gap: 18,
        }}
      >
        {icon ? <div style={{ fontSize: 54 }}>{icon}</div> : null}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div
            style={{
              fontFamily: FONT_STACK,
              fontSize: 50,
              fontWeight: 800,
              color: textColor,
              lineHeight: 1.1,
              letterSpacing: 0.2,
            }}
          >
            {text}
          </div>
          {subtext ? (
            <div
              style={{
                fontFamily: FONT_STACK,
                fontSize: 28,
                fontWeight: 500,
                color: textColor,
                opacity: 0.75,
              }}
            >
              {subtext}
            </div>
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
}
