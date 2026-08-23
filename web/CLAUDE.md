# MiraMedia frontend — agent guide

Style and tooling:
- 2-space indent throughout `web/`.
- oxlint / oxfmt for lint and format — NOT eslint or prettier (`make frontend-lint`).
- `.oxfmtrc.json` ignores `**/*.md` and `**/*.mdx`; oxfmt mangles MDX — never format docs content.

Patterns:
- Complex hooks: facade file (`use-imports-queue.ts`) delegating to `-actions` and `-observation` modules — copy this decomposition.
- API calls: openapi-fetch resolves with `{ data, error }` and does not throw on 4xx/5xx — always check `error` (see `src/lib/bulk-mutate.ts`).

Testing:
- `pnpm test` — vitest, Node-only, no backend or browser.
- `pnpm run test:e2e` — Playwright browser smoke in `web/e2e/`.

Next.js:
- Read `node_modules/next/dist/docs/` before writing any Next code — APIs and conventions differ from training data.

@AGENTS.md
