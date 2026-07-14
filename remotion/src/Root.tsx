import React from "react";
import { Composition } from "remotion";
import { catalog } from "./templates/catalog";
import BespokeRuntime from "./bespoke/Runtime";
import {
  calculateOverlayMetadata,
  DEFAULT_DURATION_IN_FRAMES,
  DEFAULT_FPS,
  DEFAULT_HEIGHT,
  DEFAULT_WIDTH,
} from "./lib/composition";

// One <Composition> per catalog entry (14 templates + the generic-caption-card
// fallback = 15) plus one generic "Bespoke" composition that dynamically
// imports a bespoke-generated module by path (src/bespoke/Runtime.tsx).
//
// Every composition uses calculateMetadata so the *actual* per-cue
// duration/fps/width/height (sent by Python on stdin to render/render_cue.ts,
// merged into inputProps before renderMedia()) drive the real render — the
// static values below are just registration-time placeholders/studio-preview
// defaults.
export const Root: React.FC = () => {
  return (
    <>
      {catalog.map((entry) => (
        <Composition
          key={entry.id}
          id={entry.id}
          component={entry.component}
          durationInFrames={Math.round(
            entry.defaultDurationS * DEFAULT_FPS,
          )}
          fps={DEFAULT_FPS}
          width={DEFAULT_WIDTH}
          height={DEFAULT_HEIGHT}
          defaultProps={{
            durationInFrames: Math.round(entry.defaultDurationS * DEFAULT_FPS),
            fps: DEFAULT_FPS,
            width: DEFAULT_WIDTH,
            height: DEFAULT_HEIGHT,
          }}
          calculateMetadata={calculateOverlayMetadata}
        />
      ))}
      <Composition
        id="Bespoke"
        component={BespokeRuntime}
        durationInFrames={DEFAULT_DURATION_IN_FRAMES}
        fps={DEFAULT_FPS}
        width={DEFAULT_WIDTH}
        height={DEFAULT_HEIGHT}
        defaultProps={{
          modulePath: "",
          durationInFrames: DEFAULT_DURATION_IN_FRAMES,
          fps: DEFAULT_FPS,
          width: DEFAULT_WIDTH,
          height: DEFAULT_HEIGHT,
        }}
        calculateMetadata={calculateOverlayMetadata}
      />
    </>
  );
};
