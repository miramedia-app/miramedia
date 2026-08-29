"use client";

import * as React from "react";
import Link from "next/link";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

export type Crumb = { label: string; href?: string };

export function DashboardHeader({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <header className="sticky top-0 z-30 flex h-16 shrink-0 items-center gap-2 bg-background/90 pt-safe-t backdrop-blur lg:static lg:bg-transparent lg:pt-0 lg:backdrop-blur-none">
      <div className="flex items-center gap-2 px-4">
        <SidebarTrigger size="icon" className="-ml-1 coarse:size-11" />
        <Separator className="mr-2 h-4 !self-center" orientation="vertical" />
        <Breadcrumb>
          <BreadcrumbList>
            {crumbs.map((c, idx) => {
              const isLast = idx === crumbs.length - 1;
              return (
                <React.Fragment key={`${c.label}-${idx}`}>
                  <BreadcrumbItem className={idx === 0 ? "hidden lg:block" : undefined}>
                    {isLast || !c.href ? (
                      <BreadcrumbPage>{c.label}</BreadcrumbPage>
                    ) : (
                      <BreadcrumbLink render={<Link href={c.href} />}>{c.label}</BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                  {!isLast && (
                    <BreadcrumbSeparator className={idx === 0 ? "hidden lg:block" : undefined} />
                  )}
                </React.Fragment>
              );
            })}
          </BreadcrumbList>
        </Breadcrumb>
      </div>
    </header>
  );
}
