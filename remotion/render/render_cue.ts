#!/usr/bin/env node
// THE entrypoint Python invokes (app/pipeline/overlays.py's
// render_overlay_batch()) for the "render_overlays" pipeline stage.
//
// Reads a JSON array of cue-job objects from stdin, bundles the Remotion
// project once, opens ONE shared headless-Chrome instance (via
// @remotion/renderer's browser-reuse API — `puppeteerInstance`), then loops
// over jobs rendering each into its own ProRes4444 alpha .mov, with each cue
// wrapped in its own try/catch so one failure never kills the batch.
//
// Usage:
//   npx tsx render/render_cue.ts --out-dir <dir> < cue-jobs.json
//
// Prints a JSON array to stdout: [{cue_id, status: "ok"|"failed", path?, error?}]
import path from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";

import { bundle } from "@remotion/bundler";
import {
  openBrowser,
  renderMedia,
  selectComposition,
  type HeadlessBrowser,
} from "@remotion/renderer";

import type { CueJob, CueRenderResult } from "../src/lib/types";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const ENTRY_POINT = path.join(REPO_ROOT, "src", "index.ts");

function parseArgs(argv: string[]): { outDir: string } {
  const idx = argv.indexOf("--out-dir");
  if (idx === -1 || !argv[idx + 1]) {
    throw new Error("Missing required argument: --out-dir <dir>");
  }
  return { outDir: path.resolve(argv[idx + 1]) };
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function validateJobs(raw: unknown): CueJob[] {
  if (!Array.isArray(raw)) {
    throw new Error("stdin payload must be a JSON array of cue jobs");
  }
  return raw.map((job, i) => {
    if (!job || typeof job !== "object") {
      throw new Error(`job[${i}] is not an object`);
    }
    const j = job as Record<string, unknown>;
    if (typeof j.cue_id !== "string" || !j.cue_id) {
      throw new Error(`job[${i}] missing string cue_id`);
    }
    if (j.kind !== "template" && j.kind !== "bespoke") {
      throw new Error(`job[${i}] (${j.cue_id}) has invalid kind: ${String(j.kind)}`);
    }
    if (typeof j.duration_s !== "number" || j.duration_s <= 0) {
      throw new Error(`job[${i}] (${j.cue_id}) missing/invalid duration_s`);
    }
    if (typeof j.fps !== "number" || j.fps <= 0) {
      throw new Error(`job[${i}] (${j.cue_id}) missing/invalid fps`);
    }
    if (typeof j.width !== "number" || typeof j.height !== "number") {
      throw new Error(`job[${i}] (${j.cue_id}) missing/invalid width/height`);
    }
    return j as unknown as CueJob;
  });
}

function compositionIdFor(job: CueJob): string {
  if (job.kind === "bespoke") return "Bespoke";
  if (!job.template_id) {
    throw new Error(`bespoke job ${job.cue_id} kind="template" missing template_id`);
  }
  return job.template_id;
}

function inputPropsFor(job: CueJob): Record<string, unknown> {
  const durationInFrames = Math.max(1, Math.round(job.duration_s * job.fps));
  const base: Record<string, unknown> = {
    ...(job.props ?? {}),
    durationInFrames,
    fps: job.fps,
    width: job.width,
    height: job.height,
  };
  if (job.kind === "bespoke") {
    if (!job.module_path) {
      throw new Error(`bespoke job ${job.cue_id} missing module_path`);
    }
    base.modulePath = job.module_path;
  }
  return base;
}

async function renderOneCue(
  job: CueJob,
  serveUrl: string,
  browser: HeadlessBrowser,
  outDir: string,
): Promise<CueRenderResult> {
  const compositionId = compositionIdFor(job);
  const inputProps = inputPropsFor(job);

  const composition = await selectComposition({
    serveUrl,
    id: compositionId,
    inputProps,
    puppeteerInstance: browser,
  });

  const outputLocation = path.join(outDir, `${job.cue_id}.mov`);

  await renderMedia({
    composition,
    serveUrl,
    codec: "prores",
    proResProfile: "4444",
    pixelFormat: "yuva444p10le",
    imageFormat: "png", // required for alpha-preserving intermediate frames
    outputLocation,
    inputProps,
    puppeteerInstance: browser,
    overwrite: true,
  });

  return { cue_id: job.cue_id, status: "ok", path: outputLocation };
}

async function main() {
  const { outDir } = parseArgs(process.argv.slice(2));
  mkdirSync(outDir, { recursive: true });

  const stdinText = await readStdin();
  let rawJobs: unknown;
  try {
    rawJobs = JSON.parse(stdinText);
  } catch (e) {
    process.stderr.write(`Failed to parse stdin as JSON: ${String(e)}\n`);
    process.exit(1);
  }
  const jobs = validateJobs(rawJobs);

  process.stderr.write(`[render_cue] bundling Remotion project...\n`);
  const serveUrl = await bundle({
    entryPoint: ENTRY_POINT,
    onProgress: () => {},
  });
  process.stderr.write(`[render_cue] bundle ready at ${serveUrl}\n`);

  process.stderr.write(`[render_cue] opening shared headless browser...\n`);
  const browser = await openBrowser("chrome");
  process.stderr.write(`[render_cue] browser ready, rendering ${jobs.length} cue(s)...\n`);

  const results: CueRenderResult[] = [];
  for (const job of jobs) {
    try {
      const result = await renderOneCue(job, serveUrl, browser, outDir);
      process.stderr.write(`[render_cue] ok: ${job.cue_id} -> ${result.path}\n`);
      results.push(result);
    } catch (e) {
      const message = e instanceof Error ? (e.stack ?? e.message) : String(e);
      process.stderr.write(`[render_cue] FAILED: ${job.cue_id}: ${message}\n`);
      results.push({ cue_id: job.cue_id, status: "failed", error: message });
    }
  }

  await browser.close({ silent: true });

  process.stdout.write(JSON.stringify(results));

  // The batch process itself always exits 0 once it completes — per-cue
  // success/failure is communicated via the JSON on stdout (which Python
  // parses), not the process exit code. A non-zero exit is reserved for
  // fatal errors that prevented the batch from running at all (bad stdin,
  // bundling failure, etc. — see the top-level .catch below).
  process.exit(0);
}

main().catch((e) => {
  process.stderr.write(`[render_cue] fatal: ${e instanceof Error ? (e.stack ?? e.message) : String(e)}\n`);
  process.exit(1);
});
