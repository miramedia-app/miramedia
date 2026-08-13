/**
 * Run openapi-typescript with TypeScript 5's compiler API.
 * openapi-typescript imports `typescript` directly; the project uses TS 7 for
 * `tsc --noEmit`, so we invoke the pnpm variant wired to typescript@5.9.3.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pnpmDir = path.join(webRoot, "node_modules/.pnpm");

const oapiStoreDir = readdirSync(pnpmDir).find((name) =>
  name.startsWith("openapi-typescript@7.13.0_typescript@5.9.3"),
);

if (!oapiStoreDir) {
  console.error("openapi-typescript + typescript@5.9.3 pnpm variant not found; run pnpm install");
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

const result = spawnSync(process.execPath, [oapiCli, ...args], {
  cwd: webRoot,
  stdio: "inherit",
});

process.exit(result.status ?? 1);
