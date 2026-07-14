#!/usr/bin/env node
// Reads src/templates/catalog.ts (the typed zod-schema registry, the source
// of truth), converts each entry's zod propsSchema to JSON Schema, computes a
// catalog_version content hash, and writes src/templates/catalog.json — the
// language-neutral manifest app/pipeline/overlay_catalog.py reads on the
// Python side.
//
// Run via `npm run catalog:build` (which invokes this through `tsx` so the
// TypeScript/TSX imports inside catalog.ts — including the React template
// components it references — resolve without a separate build step).
//
// NOTE (deviation from the original plan): the plan called for the
// `zod-to-json-schema` npm package. This project pins zod v4 (the current
// stable major), and empirically `zod-to-json-schema@3.25.2` — despite
// advertising a `zod: "^3.25.28 || ^4"` peer range — produces empty/broken
// schemas against zod v4's rewritten internals (verified directly: it
// returns `{}` for a simple z.object() under zod v4, vs. a correct schema
// under zod v3). Zod v4 ships its own native `z.toJSONSchema()`, which
// produces correct output and needs no extra dependency, so we use that
// instead and don't install zod-to-json-schema at all.
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";

import { catalog } from "../src/templates/catalog.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const TEMPLATES_DIR = path.join(REPO_ROOT, "src", "templates");
const CATALOG_TS_PATH = path.join(TEMPLATES_DIR, "catalog.ts");
const OUT_PATH = path.join(TEMPLATES_DIR, "catalog.json");

function collectTemplateSourceFiles() {
  // Every templates/{id}/index.tsx, in a stable (sorted) order, plus
  // catalog.ts itself.
  const entries = readdirSync(TEMPLATES_DIR, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort();

  const files = [];
  for (const dir of entries) {
    const indexPath = path.join(TEMPLATES_DIR, dir, "index.tsx");
    try {
      if (statSync(indexPath).isFile()) {
        files.push(indexPath);
      }
    } catch {
      // no index.tsx in this directory — skip
    }
  }
  files.push(CATALOG_TS_PATH);
  return files;
}

function computeCatalogVersion() {
  const files = collectTemplateSourceFiles();
  const hash = createHash("sha256");
  for (const f of files) {
    hash.update(readFileSync(f, "utf8"));
  }
  return hash.digest("hex").slice(0, 12);
}

function buildManifest() {
  const catalogVersion = computeCatalogVersion();

  const templates = catalog.map((entry) => {
    const propsJsonSchema = z.toJSONSchema(entry.propsSchema, {
      target: "draft-7",
    });

    return {
      id: entry.id,
      title: entry.title,
      category: entry.category,
      applicable_cue_types: entry.applicableCueTypes,
      description: entry.description,
      match_hints: entry.matchHints,
      props_schema: propsJsonSchema,
      duration_mode: entry.durationMode,
      default_duration_s: entry.defaultDurationS,
      is_fallback: entry.isFallback,
    };
  });

  return {
    catalog_version: catalogVersion,
    templates,
  };
}

function main() {
  const manifest = buildManifest();
  writeFileSync(OUT_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  console.log(
    `Wrote ${path.relative(REPO_ROOT, OUT_PATH)} (${manifest.templates.length} templates, catalog_version=${manifest.catalog_version})`,
  );
}

main();
