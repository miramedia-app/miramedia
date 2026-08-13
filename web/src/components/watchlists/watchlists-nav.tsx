"use client";

import * as React from "react";

import { DashboardHeader, type Crumb } from "@/components/dashboard-header";
import { cn } from "@/lib/utils";
import { WATCHLISTS_BASE } from "./watchlists-routes";

/**
 * Shared chrome for Watchlists routes: app-wide header, then page content.
 */
export function WatchlistsPageShell({
  crumbs,
  children,
  mainClassName,
}: {
  /** Crumbs after Dashboard › Watchlists (e.g. playlist name on detail). */
  crumbs?: Crumb[];
  children: React.ReactNode;
  mainClassName?: string;
}) {
  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Watchlists", href: WATCHLISTS_BASE },
          ...(crumbs ?? []),
        ]}
      />
      <main className={cn("flex flex-1 flex-col p-4 pt-0", mainClassName)}>{children}</main>
    </>
  );
}
