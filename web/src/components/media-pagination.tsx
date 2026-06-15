"use client";

import * as React from "react";
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

function getPageNumbers(current: number, totalPages: number): (number | "ellipsis")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
  const pages: (number | "ellipsis")[] = [];
  pages.push(1);
  if (current > 3) pages.push("ellipsis");
  const start = Math.max(2, current - 1);
  const end = Math.min(totalPages - 1, current + 1);
  for (let i = start; i <= end; i++) pages.push(i);
  if (current < totalPages - 2) pages.push("ellipsis");
  pages.push(totalPages);
  return pages;
}

export function MediaPagination({
  page,
  totalPages,
  onPageChange,
  total,
  pageSize,
  pageSizeOptions,
  onPageSizeChange,
  className,
}: {
  page: number;
  totalPages: number;
  onPageChange: (next: number) => void;
  total?: number;
  pageSize?: number;
  pageSizeOptions?: number[];
  onPageSizeChange?: (next: number) => void;
  className?: string;
}) {
  const hasPageSizeSelector = pageSize != null && !!pageSizeOptions && !!onPageSizeChange;
  if (!hasPageSizeSelector && totalPages <= 1 && (!total || total <= (pageSize ?? 0))) return null;
  const pages = getPageNumbers(page, Math.max(1, totalPages));
  const start = total != null && pageSize ? (total === 0 ? 0 : (page - 1) * pageSize + 1) : null;
  const end = total != null && pageSize ? Math.min(page * pageSize, total) : null;

  return (
    <div
      className={cn(
        "grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-1 text-xs text-muted-foreground",
        className,
      )}
    >
      {start != null && total != null ? (
        <div className="justify-self-start tabular-nums">
          Showing{" "}
          <span className="font-medium text-foreground">
            {start}–{end}
          </span>{" "}
          of <span className="font-medium text-foreground">{total}</span>
        </div>
      ) : (
        <div />
      )}
      <div className="flex items-center gap-1 justify-self-center">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          aria-label="Previous page"
        >
          <ChevronLeftIcon className="h-4 w-4" />
        </Button>
        {pages.map((p, i) =>
          p === "ellipsis" ? (
            <span key={`e${i}`} className="px-1.5 text-muted-foreground/60">
              …
            </span>
          ) : (
            <Button
              key={p}
              variant={p === page ? "secondary" : "ghost"}
              size="sm"
              className="h-7 min-w-7 px-2 text-xs tabular-nums"
              onClick={() => onPageChange(p)}
            >
              {p}
            </Button>
          ),
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          aria-label="Next page"
        >
          <ChevronRightIcon className="h-4 w-4" />
        </Button>
      </div>
      {pageSize != null && pageSizeOptions && onPageSizeChange ? (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="sm" className="h-7 gap-1 justify-self-end text-xs">
                Items: <span className="font-medium text-foreground tabular-nums">{pageSize}</span>
              </Button>
            }
          />
          <DropdownMenuContent align="end">
            <DropdownMenuGroup>
              <DropdownMenuRadioGroup
                value={String(pageSize)}
                onValueChange={(v) => onPageSizeChange(Number(v))}
              >
                {pageSizeOptions.map((n) => (
                  <DropdownMenuRadioItem key={n} value={String(n)}>
                    {n} per page
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <div />
      )}
    </div>
  );
}
