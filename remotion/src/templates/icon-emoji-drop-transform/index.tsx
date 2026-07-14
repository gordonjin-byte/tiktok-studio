import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { z } from "zod";
import { FONT_STACK, enterExitProgress } from "../../lib/anim";

export const iconEmojiDropTransformSchema = z
  .object({
    fromIcon: z.string().min(1).max(8),
    toIcon: z.string().max(8).optional(),
    toText: z.string().max(40).optional(),
    transformStyle: z.enum(["morph", "blend", "grind"]).default("morph"),
    labelBefore: z.string().max(40).optional(),
    labelAfter: z.string().max(40).optional(),
  })
  .refine((v) => v.toIcon || v.toText, {
    message: "either toIcon or toText must be provided",
  });

export type IconEmojiDropTransformProps = z.infer<
  typeof iconEmojiDropTransformSchema
>;

export default function IconEmojiDropTransform(
  props: IconEmojiDropTransformProps,
) {
  const { fromIcon, toIcon, toText, transformStyle, labelBefore, labelAfter } =
    props;
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();

  // Drop-in for the "from" icon over the first ~25% of the clip.
  const dropFrames = Math.max(10, Math.round(durationInFrames * 0.22));
  const dropP = spring({
    frame,
    fps,
    config: { damping: 11, stiffness: 160, mass: 0.6 },
    durationInFrames: dropFrames,
  });
  const dropY = interpolate(dropP, [0, 1], [-220, 0]);

  // Transform window: middle third of the clip.
  const transformStart = dropFrames + 4;
  const transformEnd = Math.max(
    transformStart + 8,
    durationInFrames - Math.round(durationInFrames * 0.18),
  );
  const tProgress = interpolate(
    frame,
    [transformStart, transformEnd],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const overallFade = enterExitProgress(frame, durationInFrames, 6, 14);

  let fromOpacity = 1;
  let toOpacity = 0;
  let fromScale = 1;
  let toScale = 0.6;
  let rotate = 0;

  if (transformStyle === "morph") {
    fromOpacity = 1 - tProgress;
    toOpacity = tProgress;
    fromScale = interpolate(tProgress, [0, 1], [1, 0.3]);
    toScale = interpolate(tProgress, [0, 1], [0.4, 1]);
    rotate = interpolate(tProgress, [0, 1], [0, 180]);
  } else if (transformStyle === "blend") {
    fromOpacity = interpolate(tProgress, [0, 0.6], [1, 0], {
      extrapolateRight: "clamp",
    });
    toOpacity = interpolate(tProgress, [0.4, 1], [0, 1], {
      extrapolateLeft: "clamp",
    });
    fromScale = 1;
    toScale = 1;
  } else {
    // grind: juddery step-wise reveal
    const steps = 6;
    const stepped = Math.floor(tProgress * steps) / steps;
    fromOpacity = 1 - stepped;
    toOpacity = stepped;
    fromScale = interpolate(stepped, [0, 1], [1, 0.7]);
    toScale = interpolate(stepped, [0, 1], [0.7, 1]);
    rotate = stepped * 40 * (Math.floor(tProgress * 20) % 2 === 0 ? 1 : -1);
  }

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          opacity: overallFade,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 22,
          transform: `translateY(${dropY}px)`,
        }}
      >
        <div
          style={{
            position: "relative",
            width: 260,
            height: 260,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              position: "absolute",
              fontSize: 150,
              opacity: fromOpacity,
              transform: `scale(${fromScale}) rotate(${rotate}deg)`,
            }}
          >
            {fromIcon}
          </div>
          <div
            style={{
              position: "absolute",
              fontSize: toText ? 56 : 150,
              fontFamily: FONT_STACK,
              fontWeight: 800,
              color: "#fff",
              opacity: toOpacity,
              transform: `scale(${toScale}) rotate(${-rotate}deg)`,
              textAlign: "center",
              maxWidth: 260,
            }}
          >
            {toIcon ?? toText}
          </div>
        </div>
        <div
          style={{
            display: "flex",
            gap: 14,
            fontFamily: FONT_STACK,
            fontSize: 30,
            fontWeight: 600,
            color: "rgba(255,255,255,0.85)",
            maxWidth: width * 0.85,
          }}
        >
          {labelBefore ? (
            <span style={{ opacity: fromOpacity }}>{labelBefore}</span>
          ) : null}
          {labelBefore && labelAfter ? <span>→</span> : null}
          {labelAfter ? (
            <span style={{ opacity: toOpacity }}>{labelAfter}</span>
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
}
