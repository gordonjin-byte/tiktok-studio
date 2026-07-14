// Shared calculateMetadata used by every <Composition> in Root.tsx. Every cue
// job from Python (render/render_cue.ts) merges durationInFrames/fps/width/
// height into inputProps alongside the template-specific props, so each
// Composition's actual runtime duration/dimensions are driven per-cue rather
// than baked in at Composition-registration time.
export interface DimensionOverrideProps {
  durationInFrames?: number;
  fps?: number;
  width?: number;
  height?: number;
  [key: string]: unknown;
}

export const DEFAULT_FPS = 30;
export const DEFAULT_WIDTH = 1080;
export const DEFAULT_HEIGHT = 1920;
export const DEFAULT_DURATION_IN_FRAMES = 60;

// A plain generic function (rather than a single fixed CalculateMetadataFunction<T>
// constant) so it can be assigned to any <Composition>'s calculateMetadata slot —
// TypeScript infers T from each Composition's own Props type at the call site,
// since every Props type here extends DimensionOverrideProps.
export function calculateOverlayMetadata<T extends DimensionOverrideProps>({
  props,
}: {
  props: T;
}) {
  const fps =
    typeof props.fps === "number" && props.fps > 0 ? props.fps : DEFAULT_FPS;
  const width =
    typeof props.width === "number" && props.width > 0
      ? Math.round(props.width)
      : DEFAULT_WIDTH;
  const height =
    typeof props.height === "number" && props.height > 0
      ? Math.round(props.height)
      : DEFAULT_HEIGHT;
  const durationInFrames =
    typeof props.durationInFrames === "number" && props.durationInFrames > 0
      ? Math.max(1, Math.round(props.durationInFrames))
      : DEFAULT_DURATION_IN_FRAMES;

  return { fps, width, height, durationInFrames, props };
}
