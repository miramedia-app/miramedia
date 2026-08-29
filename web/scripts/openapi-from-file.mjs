/**
 * Run openapi-typescript with TypeScript 5's compiler API.
 * openapi-typescript imports `typescript` directly; the project uses TS 7 for
 * `tsc --noEmit`, so we invoke the pnpm variant wired to typescript@5.9.3.
 *
 * Fresh clones (and some worktree node_modules symlinks) only materialize
 * `openapi-typescript@7.13.0_typescript@7.0.2`. In that case we register a
 * loader that redirects `typescript` to the `typescript5` alias (5.9.3).
 */
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pnpmDir = path.join(webRoot, "node_modules/.pnpm");

const oapiStoreDir =
  readdirSync(pnpmDir).find((name) =>
    name.startsWith("openapi-typescript@7.13.0_typescript@5.9.3"),
  ) ?? readdirSync(pnpmDir).find((name) => name.startsWith("openapi-typescript@7.13.0_"));

if (!oapiStoreDir) {
  console.error("openapi-typescript@7.13.0 pnpm variant not found; run pnpm install");
  process.exit(1);
}

const oapiCli = path.join(pnpmDir, oapiStoreDir, "node_modules/openapi-typescript/bin/cli.js");

if (!existsSync(oapiCli)) {
  console.error(`openapi-typescript CLI missing at ${oapiCli}`);
  process.exit(1);
}

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("usage: node scripts/openapi-from-file.mjs <input> -o <output>");
  process.exit(1);
}

const hooks = path.join(webRoot, "scripts/openapi-ts5-hooks.mjs");
const needsTs5Shim = !oapiStoreDir.includes("typescript@5.9.3") && existsSync(hooks);
const nodeArgs = needsTs5Shim
  ? [
      "--import",
      `data:text/javascript,import { register } from "node:module"; await register(${JSON.stringify(pathToFileURL(hooks).href)});`,
      oapiCli,
      ...args,
    ]
  : [oapiCli, ...args];
const result = spawnSync(process.execPath, nodeArgs, {
  cwd: webRoot,
  stdio: "inherit",
});

process.exit(result.status ?? 1);
