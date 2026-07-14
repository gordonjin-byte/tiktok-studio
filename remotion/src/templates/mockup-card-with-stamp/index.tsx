import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const mockupCardWithStampSchema = z.object({
  mockupType: z.enum(["email", "chat", "browser", "app"]).default("email"),
  headline: z.string().min(1).max(80),
  bodyText: z.string().max(200).optional(),
  stampType: z.enum(["x", "check", "custom"]).default("x"),
  stampText: z.string().max(20).optional(),
  stampColor: z.string().default("#ff3355"),
});

export type MockupCardWithStampProps = z.infer<
  typeof mockupCardWithStampSchema
>;

const CHROME_LABEL: Record<string, string> = {
  email: "New Message",
  chat: "Messages",
  browser: "example.com",
  app: "App",
};

export default function MockupCardWithStamp(props: MockupCardWithStampProps) {
  const { mockupType, headline, bodyText, stampType, stampText, stampColor } =
    props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();

  const cardP = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 150, mass: 0.6 },
  });
  const cardOutFrames = Math.max(4, Math.min(12, Math.round(durationInFrames * 0.22)));
  const cardOpacity = enterExitProgress(frame, durationInFrames, 10, cardOutFrames);
  const cardY = interpolate(cardP, [0, 1], [40, 0]);

  // The stamp slams on AFTER the card has settled, but must still finish
  // (spring settle + visible hold) before the card's own fade-out starts —
  // scale the delay to duration so it never lands during/after the fade.
  const stampDelay = Math.max(6, Math.min(16, Math.round(durationInFrames * 0.22)));
  const stampP = spring({
    frame: frame - stampDelay,
    fps,
    config: { damping: 9, stiffness: 260, mass: 0.5 },
  });
  const stampRampLen = Math.max(3, Math.min(4, Math.round(durationInFrames * 0.08)));
  const stampScale = interpolate(stampP, [0, 1], [2.2, 1]);
  const stampOpacity = interpolate(frame, [stampDelay, stampDelay + stampRampLen], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const stampRotate = interpolate(stampP, [0, 1], [-18, -8]);

  const stampGlyph =
    stampType === "x" ? "✗" : stampType === "check" ? "✓" : stampText ?? "★";

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          position: "relative",
          width: width * 0.86,
          opacity: cardOpacity,
          transform: `translateY(${cardY}px)`,
        }}
      >
        <div
          style={{
            borderRadius: 20,
            overflow: "hidden",
            background: "#15171c",
            border: "2px solid rgba(255,255,255,0.12)",
            boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
          }}
        >
          <div
            style={{
              background: "#22252c",
              padding: "14px 20px",
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}
          >
            <div style={{ display: "flex", gap: 6 }}>
              {["#ff5f56", "#ffbd2e", "#27c93f"].map((c) => (
                <div
                  key={c}
                  style={{
                    width: 12,
                    height: 12,
                    borderRadius: "50%",
                    background: c,
                  }}
                />
              ))}
            </div>
            <div
              style={{
                fontFamily: FONT_STACK,
                fontSize: 20,
                color: "rgba(255,255,255,0.6)",
              }}
            >
              {CHROME_LABEL[mockupType]}
            </div>
          </div>
          <div style={{ padding: "30px 28px 36px" }}>
            <div
              style={{
                fontFamily: FONT_STACK,
                fontWeight: 800,
                fontSize: 38,
                color: "#fff",
                marginBottom: bodyText ? 14 : 0,
                lineHeight: 1.2,
              }}
            >
              {headline}
            </div>
            {bodyText ? (
              <div
                style={{
                  fontFamily: FONT_STACK,
                  fontSize: 26,
                  color: "rgba(255,255,255,0.72)",
                  lineHeight: 1.4,
                }}
              >
                {bodyText}
              </div>
            ) : null}
          </div>
        </div>
        <div
          style={{
            position: "absolute",
            top: "-14%",
            right: "-8%",
            opacity: stampOpacity,
            transform: `scale(${stampScale}) rotate(${stampRotate}deg)`,
          }}
        >
          <div
            style={{
              width: 140,
              height: 140,
              borderRadius: stampType === "custom" ? 20 : "50%",
              border: `8px solid ${stampColor}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: FONT_STACK,
              fontWeight: 900,
              fontSize: stampType === "custom" ? 32 : 84,
              color: stampColor,
              background: "rgba(10,10,12,0.55)",
            }}
          >
            {stampGlyph}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
}
