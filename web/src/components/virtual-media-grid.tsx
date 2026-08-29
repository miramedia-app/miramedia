"use client";

import * as React from "react";
import { useWindowVirtualizer } from "@tanstack/react-virtual";

/**
 * Poster ladder for the media library grid. Desktop (lg+) is capped at 5
 * columns on purpose: page sizes are 20/50/100/200, all multiples of 5, so
 * the last row of a page stays full. `MEDIA_GRID_COLUMNS_CLASS` and
 * `MEDIA_GRID_BREAKPOINT_COLUMNS` MUST stay in sync: the virtualizer chunks
 * rows in JS from the same column counts the CSS grid renders, otherwise rows
 * overflow or leave holes. Mirrored by `media-grid-skeleton.tsx`.
 */
export const MEDIA_GRID_COLUMNS_CLASS =
  "grid-cols-2 sm:grid-cols-3 md:grid-cols-3 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5";
export const MEDIA_GRID_GAP_CLASS = "gap-3 md:gap-4";

/** Column count per Tailwind breakpoint (base first); mirrors `MEDIA_GRID_COLUMNS_CLASS`. */
export const MEDIA_GRID_BREAKPOINT_COLUMNS = {
  base: 2,
  sm: 3,
  md: 3,
  lg: 3,
  xl: 4,
  "2xl": 5,
} as const;

export function useGridColumnCount(): number {
  const [cols, setCols] = React.useState<number>(MEDIA_GRID_BREAKPOINT_COLUMNS.xl);

  React.useEffect(() => {
    const mqSm = window.matchMedia("(min-width: 640px)");
    const mqMd = window.matchMedia("(min-width: 768px)");
    const mqLg = window.matchMedia("(min-width: 1024px)");
    const mqXl = window.matchMedia("(min-width: 1280px)");
    const mq2xl = window.matchMedia("(min-width: 1536px)");

    const update = () => {
      if (mq2xl.matches) setCols(MEDIA_GRID_BREAKPOINT_COLUMNS["2xl"]);
      else if (mqXl.matches) setCols(MEDIA_GRID_BREAKPOINT_COLUMNS.xl);
      else if (mqLg.matches) setCols(MEDIA_GRID_BREAKPOINT_COLUMNS.lg);
      else if (mqMd.matches) setCols(MEDIA_GRID_BREAKPOINT_COLUMNS.md);
      else if (mqSm.matches) setCols(MEDIA_GRID_BREAKPOINT_COLUMNS.sm);
      else setCols(MEDIA_GRID_BREAKPOINT_COLUMNS.base);
    };

    update();
    for (const mq of [mqSm, mqMd, mqLg, mqXl, mq2xl]) {
      mq.addEventListener("change", update);
    }
    return () => {
      for (const mq of [mqSm, mqMd, mqLg, mqXl, mq2xl]) {
        mq.removeEventListener("change", update);
      }
    };
  }, []);

  return cols;
}

type VirtualMediaGridProps<T> = {
  items: T[];
  getKey: (item: T, index: number) => string;
  estimateRowHeight?: number;
  renderItem: (item: T) => React.ReactNode;
  className?: string;
};

/** Window-scrolled virtualized grid for media cards on list pages. */
export function VirtualMediaGrid<T>({
  items,
  getKey,
  estimateRowHeight = 380,
  renderItem,
  className,
}: VirtualMediaGridProps<T>) {
  const columnCount = useGridColumnCount();
  const listRef = React.useRef<HTMLDivElement>(null);

  const rows = React.useMemo(() => {
    const out: T[][] = [];
    for (let i = 0; i < items.length; i += columnCount) {
      out.push(items.slice(i, i + columnCount));
    }
    return out;
  }, [items, columnCount]);

  // `listRef.current` is null on the first render, so reading offsetTop inline
  // pins the virtual window to 0 and never re-measures when the toolbar above
  // the grid changes height. Measure after layout and on every resize instead.
  const [scrollMargin, setScrollMargin] = React.useState(0);
  React.useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const measure = () => setScrollMargin(el.offsetTop);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    observer.observe(el);
    return () => observer.disconnect();
  }, [rows.length]);

  const virtualizer = useWindowVirtualizer({
    count: rows.length,
    estimateSize: () => estimateRowHeight,
    overscan: 2,
    scrollMargin,
  });

  if (rows.length === 0) {
    return null;
  }

  return (
    <div ref={listRef} className={className}>
      <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const rowItems = rows[virtualRow.index] ?? [];
          return (
            <div
              key={virtualRow.key}
              data-index={virtualRow.index}
              ref={virtualizer.measureElement}
              className={`absolute top-0 left-0 grid w-full auto-rows-min ${MEDIA_GRID_GAP_CLASS} ${MEDIA_GRID_COLUMNS_CLASS} [content-visibility:auto]`}
              style={{
                transform: `translateY(${virtualRow.start - virtualizer.options.scrollMargin}px)`,
                // Pair with content-visibility:auto so skipped rows keep a
                // realistic height; otherwise they collapse and feed near-zero
                // measurements back to the virtualizer, jumping the scroll.
                containIntrinsicSize: `auto ${estimateRowHeight}px`,
              }}
            >
              {rowItems.map((item, colIdx) => {
                const globalIdx = virtualRow.index * columnCount + colIdx;
                return (
                  <React.Fragment key={getKey(item, globalIdx)}>{renderItem(item)}</React.Fragment>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
