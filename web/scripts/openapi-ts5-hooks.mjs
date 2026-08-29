import { pathToFileURL } from "node:url";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SHIM = "openapi-ts5:typescript";
const registerUrl = pathToFileURL(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "openapi-ts5-hooks.mjs"),
).href;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "typescript") {
    return { shortCircuit: true, url: SHIM };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url === SHIM) {
    const source = `
      import { createRequire } from "node:module";
      const ts = createRequire(${JSON.stringify(registerUrl)})("typescript5");
      export default ts;
    `;
    return { format: "module", source, shortCircuit: true };
  }
  return nextLoad(url, context);
}
