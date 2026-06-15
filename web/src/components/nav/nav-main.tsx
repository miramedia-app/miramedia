"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronRight, Plus, type LucideIcon } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from "@/components/ui/sidebar";

export type NavItem = {
  title: string;
  url: string;
  icon: LucideIcon;
  isActive?: boolean;
  addUrl?: string;
  items?: { title: string; url: string; icon?: LucideIcon }[];
};

export function NavMain({ items }: { items: NavItem[] }) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel />
      <SidebarMenu>
        {items.map((item) => {
          const Icon = item.icon;
          if (item.items) {
            return (
              <Collapsible key={item.title} className="group/collapsible">
                <SidebarMenuItem>
                  <CollapsibleTrigger render={<SidebarMenuButton tooltip={item.title} />}>
                    <Icon />
                    <span>{item.title}</span>
                    <ChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90" />
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <SidebarMenuSub>
                      {item.items.map((sub) => {
                        const SubIcon = sub.icon;
                        return (
                          <SidebarMenuSubItem key={sub.title}>
                            <SidebarMenuSubButton render={<Link href={sub.url} />}>
                              {SubIcon && <SubIcon />}
                              <span>{sub.title}</span>
                            </SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                        );
                      })}
                    </SidebarMenuSub>
                  </CollapsibleContent>
                </SidebarMenuItem>
              </Collapsible>
            );
          }
          return (
            <SidebarMenuItem key={item.title}>
              <SidebarMenuButton render={<Link href={item.url} />} tooltip={item.title}>
                <Icon />
                <span>{item.title}</span>
              </SidebarMenuButton>
              {item.addUrl && (
                <SidebarMenuAction
                  render={<Link href={item.addUrl} />}
                  className="top-1/2 right-0 size-7 -translate-y-1/2 peer-data-[size=default]/menu-button:top-1/2"
                >
                  <Plus />
                  <span className="sr-only">Add</span>
                </SidebarMenuAction>
              )}
            </SidebarMenuItem>
          );
        })}
      </SidebarMenu>
    </SidebarGroup>
  );
}
