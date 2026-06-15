"use client";

import * as React from "react";
import { useWindowVirtualizer } from "@tanstack/react-virtual";

/** Tailwind breakpoints for the media library grid (matches movies/shows pages). */
function useGridColumnCount(): number {
  const [cols, setCols] = React.useState(5);

  React.useEffect(() => {
    const mqSm = window.matchMedia("(min-width: 640px)");
    const mqMd = window.matchMedia("(min-width: 768px)");
    const mqLg = window.matchMedia("(min-width: 1024px)");
    const mqXl = window.matchMedia("(min-width: 1280px)");
    const mq2xl = window.matchMedia("(min-width: 1536px)");

    const update = () => {
      if (mq2xl.matches) setCols(5);
      else if (mqXl.matches) setCols(4);
      else if (mqLg.matches) setCols(3);
      else if (mqMd.matches) setCols(2);
      else if (mqSm.matches) setCols(1);
      else setCols(1);
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

  const virtualizer = useWindowVirtualizer({
    count: rows.length,
    estimateSize: () => estimateRowHeight,
    overscan: 2,
    scrollMargin: listRef.current?.offsetTop ?? 0,
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
              className="absolute top-0 left-0 grid w-full auto-rows-min gap-4 [content-visibility:auto] sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5"
              style={{
                transform: `translateY(${virtualRow.start - virtualizer.options.scrollMargin}px)`,
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
