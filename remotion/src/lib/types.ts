// Shared prop shape that every template component AND every bespoke-generated
// component must accept. Template-specific props are unioned in on top of this
// on a per-template basis (see each templates/{id}/index.tsx's zod schema).
//
// durationInFrames/fps/width/height are always injected by Root.tsx from the
// Composition's calculateMetadata (derived from the cue job's duration_s/fps/
// width/height sent by Python) so every component can compute progress with
// useCurrentFrame()/useVideoConfig() without re-deriving these itself.
export interface BespokeProps {
  durationInFrames: number;
  fps: number;
  width: number;
  height: number;
  [key: string]: unknown;
}

// The stdin job contract Python sends to render/render_cue.ts (kept here too
// so the render entrypoint and any future Node-side consumer share one type).
export type CueKind = "template" | "bespoke";

export interface CueJob {
  cue_id: string;
  kind: CueKind;
  template_id?: string;
  module_path?: string; // relative to src/, e.g. "generated/{video_id}/{cue_id}" (no extension)
  duration_s: number;
  fps: number;
  width: number;
  height: number;
  props: Record<string, unknown>;
}

export interface CueRenderResult {
  cue_id: string;
  status: "ok" | "failed";
  path?: string;
  error?: string;
}
