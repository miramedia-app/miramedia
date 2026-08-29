<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Mobile rules

- **Breakpoint semantics.** `useIsMobile()` (`src/hooks/use-mobile.ts`) is true when the viewport
  is `< 1024px` (Tailwind `lg`) OR the device has a coarse pointer, so a phone in landscape stays
  "mobile". In classes use `max-lg:`/`lg:` for the same split; do NOT use `md:` for layout that
  must agree with the tab bar or `useIsMobile()`.
- **`coarse:` variant.** `@custom-variant coarse (@media (pointer: coarse))` in `globals.css`.
  Use it for touch density (`coarse:h-11`, `coarse:min-h-11`) regardless of width.
- **Targets.** Every tappable control is at least 44px on touch (`coarse:` sizing lives in
  `ui/button.tsx`, `ui/input.tsx`, `ui/checkbox.tsx`, `ui/select.tsx`, `ui/dropdown-menu.tsx`).
  Don't shrink them with ad-hoc `h-8` overrides on mobile.
- **Tab bar clearance.** `(dashboard)/layout.tsx` pads `SidebarInset` with
  `pb-[calc(3.5rem+env(safe-area-inset-bottom))] lg:pb-0` for the `MobileTabBar`. Anything
  fixed to the bottom on mobile sits at `bottom-14` (bulk bar, sticky save bars) and uses the
  `pb-safe-b` / `env(safe-area-inset-bottom)` tokens, switching to `lg:bottom-4` / `lg:pb-0`.
- **DataList.** Columns take `mobile: { role: "title" | "subtitle" | "meta" | "hidden", order? }`
  and the list takes `mobile: { mode: "cards" | "scroll" }` (`data-list/types.ts`). Default is
  `cards` (`DataListCardRow`, header hidden, >2 actions collapse into a menu); use `scroll` only
  for genuinely tabular data (Logs). Always annotate roles on new column configs.
- **Toolbars.** Search slot is `basis-full sm:basis-auto`; filters collapse into a Drawer button
  on mobile; the primary action stays visible (icon-only + `sr-only` label at base). Pages that
  use `DataListSearchFilter` outside `DataListToolbar` must pass `className="basis-full sm:basis-auto"`.
- **Dialogs.** Use `ui/responsive-dialog.tsx` (Dialog on desktop, vaul Drawer bottom sheet on
  mobile; same API as `ui/dialog`). Never hard-code `min-w-[600px]`; use `sm:min-w-…` +
  `max-w-[95vw]`.
- **Media grid.** `virtual-media-grid.tsx` keeps `MEDIA_GRID_COLUMNS_CLASS` (Tailwind ladder) and
  `MEDIA_GRID_BREAKPOINT_COLUMNS` (JS matchMedia ladder) in sync by hand — change both. Desktop
  is capped at 5 columns: page sizes (20/50/100/200) are multiples of 5, so more columns leave an
  orphan last row.
- **Verification.** `pnpm exec playwright test --project=mobile --project=mobile-landscape`
  (`e2e/mobile-layout.spec.ts`, no-horizontal-overflow guard on every dashboard route; the
  fixture state boots logged-out, so set `state.session.loggedOut = false`).

