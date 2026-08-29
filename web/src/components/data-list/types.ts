import type * as React from "react";

export type FilterOperator = "is" | "is_not" | "includes" | "excludes";

export interface FacetOption {
  value: string;
  label: string;
  /** Optional icon node shown to the left of the label. */
  icon?: React.ReactNode;
  /** Optional small count badge shown after the label. */
  count?: number;
  /** Optional keywords for fuzzy search in the cmdk popover. */
  keywords?: string[];
}

export interface FacetDef<T> {
  id: string;
  label: string;
  /** Icon shown on the facet chip + popover entry. */
  icon?: React.ReactNode;
  /** Static options. If you need async, pre-resolve and pass them in. */
  options: FacetOption[];
  /** Default operator (defaults to 'includes'). */
  defaultOperator?: FilterOperator;
  /** Supported operators (defaults to ['includes','excludes']). */
  operators?: FilterOperator[];
  /** Predicate run per item per active facet value. */
  predicate: (item: T, values: string[], operator: FilterOperator) => boolean;
}

export interface ActiveFilter {
  facetId: string;
  operator: FilterOperator;
  values: string[];
}

export interface ColumnDef<T> {
  id: string;
  /** Header label. Use null for unlabeled fixed columns (checkbox, status icon). */
  header?: React.ReactNode;
  /** CSS grid track for this column. Use 'minmax(0,1fr)' for the title column. */
  width: string;
  /** Cell renderer. */
  render: (item: T, ctx: { focused: boolean; selected: boolean }) => React.ReactNode;
  /** Right-align the column. */
  align?: "start" | "end" | "center";
  /** Sort comparator. If provided, header becomes sortable. */
  sort?: (a: T, b: T) => number;
  /** Hide on small screens. */
  hideBelow?: "sm" | "md" | "lg" | "xl";
  /** Tabular numerals — useful for IDs/dates/counts. */
  mono?: boolean;
  /**
   * Placement inside the mobile card row (see `data-list-card-row.tsx`).
   * When omitted, roles are inferred: the first column whose width is a
   * flexible track (`1fr` / `minmax`) — or simply the first column — becomes
   * the title and every other column is rendered as a meta chip.
   */
  mobile?: ColumnMobileConfig<T>;
}

/**
 * Card-row slot for a column on mobile:
 * - `title`    first line, bold, clamps to two lines
 * - `status`   pill right-aligned on the title line
 * - `subtitle` second line, muted
 * - `progress` full-width line (progress bars)
 * - `meta`     small `·`-separated chips on the last line
 * - `hidden`   not rendered
 */
export type ColumnMobileRole = "title" | "subtitle" | "status" | "progress" | "meta" | "hidden";

export interface ColumnMobileConfig<T = unknown> {
  role: ColumnMobileRole;
  /** Ordering among columns that share a role (lower first). */
  order?: number;
  /**
   * Mobile-specific renderer. Use when the desktop cell (icons, ids, mono
   * paths, multi-line stacks) does not read well as a card line.
   */
  render?: (item: T, ctx: { focused: boolean; selected: boolean }) => React.ReactNode;
}

/** One entry of the mobile `⋯` action sheet. */
export interface MobileAction {
  id: string;
  label: string;
  icon?: React.ReactNode;
  onSelect: () => void;
  destructive?: boolean;
  disabled?: boolean;
  /**
   * Render this action as a 44px icon button inline on the card (right edge)
   * instead of inside the `⋯` sheet. Any remaining actions stay reachable via
   * a compact `⋯` next to it. Only the first `primary` action is inlined.
   */
  primary?: boolean;
}

export type DataListMobileMode = "cards" | "scroll";

export interface DataListMobileConfig {
  /**
   * `cards` (default) renders each row as a stacked card driven by
   * `ColumnDef.mobile`. `scroll` keeps the grid and adds horizontal scroll —
   * for lists that are wide by nature (logs).
   */
  mode?: DataListMobileMode;
  /** Explicit min width for `scroll` mode; defaults to the sum of px tracks. */
  minWidth?: number;
}

export interface GroupByDef<T> {
  id: string;
  label: string;
  /** Returns a stable group key + display label + sort order for an item. */
  getGroup: (item: T) => { key: string; label: React.ReactNode; sortOrder?: number };
}

export interface BulkAction<T> {
  id: string;
  label: string;
  icon?: React.ReactNode;
  variant?: "default" | "secondary" | "destructive" | "outline" | "ghost";
  /** Called with the selected items. */
  onRun: (items: T[]) => void | Promise<void>;
  /** Disable conditionally. */
  disabled?: boolean;
}

export interface SortOption<T> {
  id: string;
  label: string;
  compare: (a: T, b: T) => number;
}

export type DataListDensity = "compact" | "standard" | "rich";
