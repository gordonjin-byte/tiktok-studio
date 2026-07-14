// Small shared animation helpers used by the templates. Kept dependency-light
// (only remotion's own interpolate/spring) so every template composes real
// entrance/exit easing instead of static text.
import { interpolate, spring, type VideoConfig } from "remotion";

export const FONT_STACK =
  "'Helvetica Neue', 'Inter', 'Segoe UI', Arial, sans-serif";

export const clamp = (v: number, lo: number, hi: number) =>
  Math.max(lo, Math.min(hi, v));

/**
 * Standard "pop in, hold, fade/slide out" envelope used by most templates.
 * Returns a 0..1 progress value that eases in over `inFrames`, holds at 1,
 * then eases out over `outFrames` at the very end of the clip.
 */
export function enterExitProgress(
  frame: number,
  durationInFrames: number,
  inFrames = 12,
  outFrames = 12,
): number {
  // Guard against short clips: if the caller's in+out ramps would together
  // exceed (or nearly exceed) the clip length, the in-ramp and out-ramp
  // overlap and the element never reaches full opacity before it starts
  // fading again — e.g. inFrames=14/outFrames=14 on a 24-frame clip peaks
  // at ~0.86 and declines from there. Scale both ramps down proportionally
  // so there's always at least a couple of frames held at full opacity.
  const maxEach = Math.max(2, Math.floor((durationInFrames - 2) / 2));
  const safeIn = Math.max(2, Math.min(inFrames, maxEach));
  const safeOut = Math.max(2, Math.min(outFrames, maxEach));

  const inProgress = interpolate(frame, [0, safeIn], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const outStart = durationInFrames - safeOut;
  const outProgress = interpolate(
    frame,
    [outStart, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  return Math.min(inProgress, outProgress);
}

/**
 * Distributes N staggered sub-animations (list items, multi-stage reveals)
 * across the portion of the clip that's actually held at full opacity by
 * `enterExitProgress` (i.e. before its out-ramp starts), so the LAST item's
 * reveal always finishes with a small buffer before the overall fade-out
 * begins — regardless of how short the clip or how many items there are.
 * Returns {delay, rampLen} for item index `i` of `count`.
 */
export function staggerStep(
  index: number,
  count: number,
  durationInFrames: number,
  opts: { startFrame?: number; endBuffer?: number; minRamp?: number; maxRamp?: number } = {},
): { delay: number; rampLen: number } {
  const startFrame = opts.startFrame ?? Math.max(2, Math.round(durationInFrames * 0.08));
  const endBuffer = opts.endBuffer ?? Math.max(4, Math.round(durationInFrames * 0.12));
  const minRamp = opts.minRamp ?? 4;
  const maxRamp = opts.maxRamp ?? 12;
  const n = Math.max(1, count);

  const available = Math.max(n * minRamp, durationInFrames - startFrame - endBuffer);
  const step = clamp(available / n, minRamp, maxRamp);
  const delay = startFrame + index * step;
  const rampLen = clamp(step, minRamp, maxRamp);
  return { delay, rampLen };
}

export function springIn(
  frame: number,
  fps: number,
  config?: Parameters<typeof spring>[0]["config"],
) {
  return spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 140, mass: 0.6, ...config },
  });
}

export function springOut(
  frame: number,
  durationInFrames: number,
  fps: number,
  outFrames = 15,
  config?: Parameters<typeof spring>[0]["config"],
) {
  const localFrame = frame - (durationInFrames - outFrames);
  if (localFrame < 0) return 0;
  return spring({
    frame: localFrame,
    fps,
    config: { damping: 16, stiffness: 160, mass: 0.5, ...config },
  });
}

export type VC = VideoConfig;
