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
  { value: "scheduler", label: "Scheduled tasks" },
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
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        <div>
          <h1 className="text-lg font-medium">Diagnostics</h1>
          <p className="text-sm text-muted-foreground">
            Read-only storage, database, and scheduler snapshot for operators.
          </p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList
            variant="line"
            className="h-auto w-full [scrollbar-width:none] justify-start gap-1 overflow-x-auto [&::-webkit-scrollbar]:hidden"
          >
            {TAB_DEFS.map(({ value, label }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="shrink-0 coarse:min-h-11 coarse:px-3"
              >
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="storage" className="flex flex-col gap-4 pt-4">
            <DiagnosticsStoragePanel />
          </TabsContent>
          <TabsContent value="database" className="flex flex-col gap-4 pt-4">
            <DiagnosticsDatabasePanel enabled={activeTab === "database"} />
          </TabsContent>
          <TabsContent value="scheduler" className="flex flex-col gap-4 pt-4">
            <DiagnosticsSchedulerPanel enabled={activeTab === "scheduler"} />
          </TabsContent>
        </Tabs>
      </main>
    </>
  );
}
