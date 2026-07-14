#!/usr/bin/env node
// Single-file compile-check for bespoke codegen's guardrail step.
//
// Usage: node scripts/validate-component.mjs <path/to/file.tsx>
//
// Type-checks (via the TypeScript compiler API, against this project's
// tsconfig.json) AND bundles (via esbuild — catches JSX/syntax errors fast)
// the given file in isolation. No headless Chrome needed just to validate a
// bespoke-generated component. This is what
// app/pipeline/bespoke_codegen.py shells out to right after writing a
// generated .tsx file, before ever attempting a real render.
//
// NOTE: this only compile-checks the file. The import-allowlist / banned-API
// static scan (react/remotion/remotion/* only; no fetch/fs/child_process/
// eval/Function(/process.) is a SEPARATE guardrail step done on the Python
// side before this script is ever invoked (plan §3.8, guardrail #1) — by
// design this script doesn't duplicate that policy check, it only answers
// "does this file compile."
//
// Exit code 0 + no stderr output => file is valid.
// Exit code 1 + compiler error(s) on stderr => invalid; Python feeds this
// error back to the LLM for one corrective retry.
import ts from "typescript";
import { build } from "esbuild";
import path from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const TSCONFIG_PATH = path.join(REPO_ROOT, "tsconfig.json");

function runTypeCheck(absTarget) {
  const configFile = ts.readConfigFile(TSCONFIG_PATH, ts.sys.readFile);
  if (configFile.error) {
    return [ts.flattenDiagnosticMessageText(configFile.error.messageText, "\n")];
  }
  const parsed = ts.parseJsonConfigFileContent(
    configFile.config,
    ts.sys,
    REPO_ROOT,
  );

  const program = ts.createProgram({
    rootNames: [absTarget],
    options: { ...parsed.options, noEmit: true },
  });

  const diagnostics = [
    ...program.getSyntacticDiagnostics(),
    ...program.getSemanticDiagnostics(),
    ...program.getGlobalDiagnostics(),
  ];

  if (diagnostics.length === 0) return [];

  return diagnostics.map((d) => {
    const message = ts.flattenDiagnosticMessageText(d.messageText, "\n");
    if (d.file && d.start !== undefined) {
      const { line, character } = d.file.getLineAndCharacterOfPosition(d.start);
      return `${path.relative(REPO_ROOT, d.file.fileName)}:${line + 1}:${character + 1}: ${message}`;
    }
    return message;
  });
}

async function runBundleCheck(absTarget) {
  await build({
    entryPoints: [absTarget],
    bundle: true,
    write: false,
    platform: "browser",
    format: "esm",
    target: "es2022",
    jsx: "automatic",
    absWorkingDir: REPO_ROOT,
    logLevel: "silent",
    // Externalize everything under node_modules (react/remotion/etc.) — this
    // check just needs to confirm THIS file's own code (imports, syntax,
    // JSX) is sound, not re-bundle the whole dependency graph. Relative
    // imports (e.g. a sibling in generated/) still resolve+check normally.
    plugins: [
      {
        name: "externalize-node-modules",
        setup(buildApi) {
          buildApi.onResolve({ filter: /.*/ }, (args) => {
            if (args.path.startsWith(".") || path.isAbsolute(args.path)) {
              return undefined;
            }
            return { path: args.path, external: true };
          });
        },
      },
    ],
  });
}

async function main() {
  const target = process.argv[2];
  if (!target) {
    process.stderr.write(
      "Usage: node scripts/validate-component.mjs <path/to/file.tsx>\n",
    );
    process.exit(1);
  }

  const absTarget = path.isAbsolute(target)
    ? target
    : path.resolve(process.cwd(), target);

  if (!existsSync(absTarget)) {
    process.stderr.write(`File not found: ${absTarget}\n`);
    process.exit(1);
  }

  const typeErrors = runTypeCheck(absTarget);
  if (typeErrors.length > 0) {
    process.stderr.write(typeErrors.join("\n") + "\n");
    process.exit(1);
  }

  try {
    await runBundleCheck(absTarget);
  } catch (err) {
    const message =
      err && typeof err === "object" && "message" in err
        ? String(err.message)
        : String(err);
    process.stderr.write(message + "\n");
    process.exit(1);
  }

  process.exit(0);
}

main();
