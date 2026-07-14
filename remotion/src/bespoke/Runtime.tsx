// Generic wrapper rendered by the "Bespoke" composition (Root.tsx). Dynamically
// imports a bespoke-generated .tsx module (written by
// app/pipeline/bespoke_codegen.py to src/generated/{video_id}/{cue_id}.tsx)
// by its module path and renders that module's default export, passing
// through all other props.
//
// Uses delayRender/continueRender (Remotion's documented pattern for async
// data-loading components) so the headless-Chrome renderer waits for the
// dynamic import to resolve before it screenshots a frame — without this the
// render would race the import and could capture blank frames.
import React, { useEffect, useState } from "react";
import { AbsoluteFill, continueRender, delayRender } from "remotion";
import type { DimensionOverrideProps } from "../lib/composition";

export interface BespokeRuntimeProps extends DimensionOverrideProps {
  // Path relative to src/, no extension, e.g. "generated/{video_id}/{cue_id}".
  modulePath: string;
}

type BespokeComponent = React.ComponentType<Record<string, unknown>>;

export default function BespokeRuntime(props: BespokeRuntimeProps) {
  const { modulePath, ...rest } = props;
  const [handle] = useState(() =>
    delayRender(`Loading bespoke module: ${modulePath}`),
  );
  const [Comp, setComp] = useState<BespokeComponent | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!modulePath) {
      const e = new Error("BespokeRuntime: modulePath prop is required");
      setError(e);
      continueRender(handle);
      return;
    }

    // The template-literal directory prefix (`../`, relative to src/bespoke/,
    // i.e. src/) is what webpack needs to build a context module it can
    // resolve at runtime by the interpolated string — this must stay a
    // literal `../` + variable + literal `.tsx` shape for webpack to bundle
    // it as a dynamic-import context covering src/generated/**.
    import(`../${modulePath}.tsx`)
      .then((mod) => {
        if (cancelled) return;
        if (!mod || typeof mod.default !== "function") {
          throw new Error(
            `Bespoke module "${modulePath}" has no valid default export`,
          );
        }
        setComp(() => mod.default as BespokeComponent);
        continueRender(handle);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        continueRender(handle);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modulePath]);

  if (error) {
    // Throwing here surfaces a clear error in renderMedia()'s per-cue
    // try/catch in render/render_cue.ts, rather than silently rendering a
    // blank frame for a broken bespoke module.
    throw error;
  }

  if (!Comp) {
    return <AbsoluteFill />;
  }

  return <Comp {...rest} modulePath={modulePath} />;
}
