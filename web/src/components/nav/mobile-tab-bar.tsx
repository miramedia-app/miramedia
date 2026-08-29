"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, Download, Home, ListChecks, Menu, Tv, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebar } from "@/components/ui/sidebar";
import { useFeatures } from "@/components/providers/features-provider";
import { isTabActive, selectMobileTabs } from "./mobile-tab-active";

const ICONS: Record<string, LucideIcon> = {
  "/dashboard": Home,
  "/dashboard/shows": Tv,
  "/dashboard/movies": Clapperboard,
  "/dashboard/watchlists": ListChecks,
  "/dashboard/torrents": Download,
};

const itemClass =
  "flex min-h-11 flex-1 flex-col items-center justify-center gap-0.5 rounded-md text-[11px] font-medium text-muted-foreground outline-hidden transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-5 [&_svg]:shrink-0";

/** Bottom tab bar for mobile layout mode; "More" opens the sidebar sheet. */
export function MobileTabBar() {
  const pathname = usePathname();
  const { toggleSidebar } = useSidebar();
  const { watchlists: watchlistsEnabled } = useFeatures();
  const tabs = selectMobileTabs(watchlistsEnabled);

  return (
    <nav
      aria-label="Primary"
      data-slot="mobile-tab-bar"
      className="fixed inset-x-0 bottom-0 z-30 flex h-[calc(3.5rem+env(safe-area-inset-bottom))] items-stretch gap-1 border-t bg-background/90 px-2 pt-1 pb-safe-b backdrop-blur lg:hidden"
    >
      {tabs.map((tab) => {
        const active = isTabActive(pathname, tab.url);
        const Icon = ICONS[tab.url] ?? Home;
        return (
          <Link
            key={tab.url}
            href={tab.url}
            aria-current={active ? "page" : undefined}
            data-active={active || undefined}
            className={cn(itemClass, active && "text-foreground")}
          >
            <Icon />
            <span>{tab.title}</span>
          </Link>
        );
      })}
      <button type="button" onClick={toggleSidebar} className={itemClass}>
        <Menu />
        <span>More</span>
      </button>
    </nav>
  );
}
