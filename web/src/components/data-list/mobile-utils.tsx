import * as React from "react";
import type { ColumnDef, ColumnMobileRole } from "./types";

export interface MobileLayout<T> {
  title: ColumnDef<T> | null;
  subtitle: ColumnDef<T> | null;
  status: ColumnDef<T> | null;
  progress: ColumnDef<T> | null;
  meta: ColumnDef<T>[];
}

/** Render a column for the card row, preferring its mobile override. */
export function renderMobileCell<T>(
  col: ColumnDef<T>,
  item: T,
  ctx: { focused: boolean; selected: boolean },
): React.ReactNode {
  return col.mobile?.render ? col.mobile.render(item, ctx) : col.render(item, ctx);
}

/** Accessible label for an icon-only action node (`title` / `aria-label`). */
export function actionNodeLabel(node: React.ReactNode): string | null {
  if (!React.isValidElement(node)) return null;
  const props = node.props as { title?: unknown; "aria-label"?: unknown };
  const label = props["aria-label"] ?? props.title;
  return typeof label === "string" && label ? label : null;
}

const FLEX_TRACK = /fr|minmax|auto/;

function byOrder<T>(a: ColumnDef<T>, b: ColumnDef<T>): number {
  return (a.mobile?.order ?? 0) - (b.mobile?.order ?? 0);
}

/**
 * Map columns onto card-row slots. Explicit `mobile.role` wins; when no
 * column declares a title, the first flexible-width column (or the first
 * column) is promoted so an un-annotated list still renders sensibly.
 */
export function resolveMobileLayout<T>(columns: ColumnDef<T>[]): MobileLayout<T> {
  const explicit = columns.filter((c) => c.mobile != null);
  const implicit = columns.filter((c) => c.mobile == null);

  const pick = (role: ColumnMobileRole) => explicit.filter((c) => c.mobile?.role === role);
  const titles = pick("title").sort(byOrder);
  const subtitles = pick("subtitle").sort(byOrder);
  const statuses = pick("status").sort(byOrder);
  const progresses = pick("progress").sort(byOrder);
  const meta = pick("meta");

  let title = titles[0] ?? null;
  let remaining = implicit;
  if (!title) {
    const inferred = implicit.find((c) => FLEX_TRACK.test(c.width)) ?? implicit[0] ?? null;
    title = inferred;
    remaining = implicit.filter((c) => c !== inferred);
  }
  // Extra explicit titles/subtitles beyond the first fall back to meta chips
  // rather than disappearing.
  const overflow = [
    ...titles.slice(1),
    ...subtitles.slice(1),
    ...statuses.slice(1),
    ...progresses.slice(1),
  ];
  return {
    title,
    subtitle: subtitles[0] ?? null,
    status: statuses[0] ?? null,
    progress: progresses[0] ?? null,
    meta: [...meta, ...overflow, ...remaining].sort(byOrder),
  };
}

/** Flatten fragments/arrays so we can count and lay out individual actions. */
export function flattenActionNodes(node: React.ReactNode): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const walk = (n: React.ReactNode) => {
    if (n == null || typeof n === "boolean") return;
    if (Array.isArray(n)) {
      n.forEach(walk);
      return;
    }
    if (React.isValidElement(n) && n.type === React.Fragment) {
      walk((n.props as { children?: React.ReactNode }).children);
      return;
    }
    out.push(n);
  };
  walk(node);
  return out;
}

/**
 * Marks a desktop action node (e.g. a play-dialog trigger) as the card row's
 * primary action: on mobile it renders inline at the right edge instead of
 * inside the `⋯` sheet. Transparent on desktop — just renders its children.
 */
export function MobilePrimaryAction({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

export function isPrimaryActionNode(node: React.ReactNode): boolean {
  return React.isValidElement(node) && node.type === MobilePrimaryAction;
}

/** Sum fixed px tracks (plus a floor for flexible ones) for `scroll` mode. */
export function scrollMinWidth(tracks: string[], flexFloor = 240): number {
  let total = 0;
  for (const t of tracks) {
    const px = /^(\d+(?:\.\d+)?)px$/.exec(t.trim());
    if (px) {
      total += Number(px[1]);
      continue;
    }
    const minmax = /^minmax\(\s*(\d+(?:\.\d+)?)px/.exec(t.trim());
    total += minmax ? Math.max(Number(minmax[1]), flexFloor) : flexFloor;
  }
  return total;
}
