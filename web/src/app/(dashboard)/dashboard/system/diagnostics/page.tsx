"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { DashboardHeader } from "@/components/dashboard-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useUser } from "@/components/providers/user-provider";
import { parseDiagnosticsTab, type DiagnosticsTab } from "@/lib/diagnostics";

import { DiagnosticsDatabasePanel } from "./database-panel";
import { DiagnosticsSchedulerPanel } from "./scheduler-panel";
import { DiagnosticsStoragePanel } from "./storage-panel";

const TAB_DEFS: ReadonlyArray<{ value: DiagnosticsTab; label: string }> = [
  { value: "storage", label: "Storage" },
  { value: "database", label: "Database" },
  { value: "scheduler", label: "Scheduled Tasks" },
];

export default function DiagnosticsPage() {
  const { user } = useUser();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeTab = parseDiagnosticsTab(searchParams);
  const setActiveTab = React.useCallback(
    (tab: string) => {
      const next = new URLSearchParams(searchParams.toString());
      if (tab === "storage") next.delete("tab");
      else next.set("tab", tab);
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const crumbs = [
    { label: "Dashboard", href: "/dashboard" },
    { label: "System", href: "/dashboard/system/users" },
    { label: "Diagnostics" },
  ];

  if (!user?.is_superuser) {
    return (
      <>
        <DashboardHeader crumbs={crumbs} />
        <main className="flex w-full flex-col gap-4 p-4 pt-0">
          <p className="text-sm text-muted-foreground">Admin access required.</p>
        </main>
      </>
    );
  }

  return (
    <>
      <DashboardHeader crumbs={crumbs} />
      <main className="flex w-full min-w-0 flex-col gap-4 p-4 pt-0">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full min-w-0">
          <TabsList className="flex h-auto w-full min-w-0 flex-row items-stretch justify-start gap-1 bg-transparent p-0 max-lg:-mx-4 max-lg:w-[calc(100%+2rem)] max-lg:max-w-[calc(100%+2rem)] max-lg:snap-x max-lg:snap-mandatory max-lg:[scrollbar-width:none] max-lg:overflow-x-auto max-lg:overscroll-x-contain max-lg:px-4 max-lg:py-1 max-lg:[&::-webkit-scrollbar]:hidden">
            {TAB_DEFS.map(({ value, label }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="relative flex-none shrink-0 justify-start rounded-md px-3 py-2 text-left text-sm font-medium data-[active]:border-border data-[active]:bg-muted data-[active]:shadow-none! max-lg:min-h-11 max-lg:snap-start coarse:min-h-11"
              >
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="storage" className="flex flex-col gap-4">
            <DiagnosticsStoragePanel />
          </TabsContent>
          <TabsContent value="database" className="flex flex-col gap-4">
            <DiagnosticsDatabasePanel enabled={activeTab === "database"} />
          </TabsContent>
          <TabsContent value="scheduler" className="flex flex-col gap-4">
            <DiagnosticsSchedulerPanel enabled={activeTab === "scheduler"} />
          </TabsContent>
        </Tabs>
      </main>
    </>
  );
}
