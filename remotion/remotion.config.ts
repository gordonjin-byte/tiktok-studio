// Remotion project config.
//
// These defaults (fps=30, 1080x1920) MUST stay in sync with the Python side's
// app/config.py (OUT_W, OUT_H, FPS). There's no automated cross-language import
// here (this file is loaded standalone by the Remotion CLI/bundler, not by
// render/render_cue.ts, which receives width/height/fps explicitly per-job from
// Python on stdin) — hardcoded intentionally, just keep it a manual sync point
// if app/config.py's OUT_W/OUT_H/FPS ever change.
import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("png");
Config.overrideWebpackConfig((c) => c);

export const DEFAULT_FPS = 30;
export const DEFAULT_WIDTH = 1080;
export const DEFAULT_HEIGHT = 1920;
