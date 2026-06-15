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
