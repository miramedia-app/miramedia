"use client";

import * as React from "react";
import Link from "next/link";
import { Logo } from "@/components/logo";
import { useQuery } from "@tanstack/react-query";
import {
  Bell,
  Clapperboard,
  Download,
  FolderInput,
  Home,
  Inbox,
  ScrollText,
  Search,
  Settings,
  Tv,
  Users,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { NavMain, type NavItem } from "./nav-main";
import { NavSystem, type NavSystemItem } from "./nav-system";

const SYSTEM_ITEMS: NavSystemItem[] = [
  { title: "Users", url: "/dashboard/system/users", icon: Users },
  { title: "Indexers", url: "/dashboard/system/indexers", icon: Search },
  { title: "Settings", url: "/dashboard/system/settings", icon: Settings },
  { title: "Logs", url: "/dashboard/system/logs", icon: ScrollText },
];
import { NavUser } from "./nav-user";
import { VersionUpdate } from "./version-update";
import { useUser } from "@/components/providers/user-provider";
import { useFeatures } from "@/components/providers/features-provider";
import apiClient from "@/lib/api/client";

export function AppSidebar(props: React.ComponentProps<typeof Sidebar>) {
  const { user } = useUser();
  const isSuperuser = !!user?.is_superuser;
  const { requests: requestsEnabled, notifications: notificationsEnabled } = useFeatures();
  const publicVersion = process.env.NEXT_PUBLIC_VERSION || "dev";

  const { data: runtimeVersionData } = useQuery({
    queryKey: ["system", "version"],
    queryFn: async ({ signal }) => {
      const { data } = await apiClient.GET("/api/v1/system/version", { signal });
      return data ?? null;
    },
    staleTime: 60 * 60 * 1000,
  });
  const displayVersion = runtimeVersionData?.version ?? publicVersion;

  const navMain = React.useMemo<NavItem[]>(() => {
    const items: NavItem[] = [
      { title: "Dashboard", url: "/dashboard", icon: Home, isActive: true },
      {
        title: "Shows",
        url: "/dashboard/shows",
        icon: Tv,
        isActive: true,
        addUrl: "/dashboard/shows/add",
      },
      {
        title: "Movies",
        url: "/dashboard/movies",
        icon: Clapperboard,
        isActive: true,
        addUrl: "/dashboard/movies/add",
      },
    ];
    if (requestsEnabled) {
      items.push({
        title: "Requests",
        url: "/dashboard/requests",
        icon: Inbox,
        isActive: true,
      });
    }
    if (isSuperuser) {
      items.push({
        title: "Torrents",
        url: "/dashboard/torrents",
        icon: Download,
        isActive: true,
      });
      items.push({
        title: "Imports",
        url: "/dashboard/imports",
        icon: FolderInput,
        isActive: true,
      });
    }
    return items;
  }, [requestsEnabled, isSuperuser]);

  return (
    <Sidebar {...props} variant="inset">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton render={<Link href="/dashboard" />} size="lg">
              <Logo className="size-[26px]! shrink-0 text-foreground" />
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">MiraMedia</span>
                <span className="truncate text-xs">{displayVersion}</span>
              </div>
            </SidebarMenuButton>
            {notificationsEnabled && (
              <SidebarMenuAction
                render={<Link href="/dashboard/notifications" />}
                className="top-1/2 right-0 size-7 -translate-y-1/2 peer-data-[size=lg]/menu-button:top-1/2"
              >
                <Bell />
                <span className="sr-only">Notifications</span>
              </SidebarMenuAction>
            )}
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain items={navMain} />
        {isSuperuser && <NavSystem items={SYSTEM_ITEMS} />}
      </SidebarContent>
      <SidebarFooter>
        {isSuperuser && <VersionUpdate />}
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  );
}
