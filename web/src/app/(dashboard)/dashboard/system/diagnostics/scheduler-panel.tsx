"use client";

import { useQuery } from "@tanstack/react-query";
import { Clock, LoaderCircle } from "lucide-react";

import { DataList } from "@/components/data-list";
import type { ColumnDef } from "@/components/data-list";
import { StatCard } from "@/components/stats/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { MetaPill } from "@/components/ui/type-pill";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";
import { DIAGNOSTICS_ERROR_MESSAGE, humanizeCron } from "@/lib/diagnostics";
import { qk } from "@/lib/query-keys";

type DiagnosticsScheduler = components["schemas"]["DiagnosticsScheduler"];
type DiagnosticsScheduledTask = components["schemas"]["DiagnosticsScheduledTask"];

export function DiagnosticsSchedulerPanel({ enabled }: { enabled: boolean }) {
  const query = useQuery({
    queryKey: qk.diagnostics.scheduler(),
    enabled,
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/diagnostics/scheduler", { signal });
      if (error) throw error;
      return data as DiagnosticsScheduler;
    },
  });

  const columns: ColumnDef<DiagnosticsScheduledTask>[] = [
    {
      id: "name",
      header: "Task",
      width: "minmax(0,1.4fr)",
      mobile: { role: "title" },
      render: (row) => <span className="text-sm font-medium capitalize">{row.display_name}</span>,
    },
    {
      id: "schedule",
      header: "Schedule",
      width: "minmax(0,1fr)",
      mobile: { role: "subtitle" },
      render: (row) => (
        <span className="text-sm text-muted-foreground" title={row.cron ?? undefined}>
          {humanizeCron(row.cron)}
        </span>
      ),
    },
    {
      id: "cron",
      header: "Cron",
      width: "140px",
      hideBelow: "lg",
      mono: true,
      mobile: { role: "hidden" },
      render: (row) => <span className="font-mono text-xs">{row.cron || "—"}</span>,
    },
    {
      id: "queued",
      header: "Queued",
      width: "80px",
      align: "end",
      mono: true,
      mobile: { role: "meta", order: 0 },
      render: (row) => (row.queued == null ? "—" : String(row.queued)),
    },
    {
      id: "broker",
      header: "Lane",
      width: "110px",
      hideBelow: "md",
      mobile: { role: "meta", order: 1 },
      render: (row) => <MetaPill>{row.broker}</MetaPill>,
    },
  ];

  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Failed to load scheduled tasks</AlertTitle>
        <AlertDescription className="flex items-center gap-2">
          {DIAGNOSTICS_ERROR_MESSAGE}
          <Button variant="outline" size="sm" onClick={() => query.refetch()}>
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  if (query.isPending) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <LoaderCircle className="h-4 w-4 animate-spin" />
        Loading scheduled tasks…
      </div>
    );
  }

  const snap = query.data;

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Cron-scheduled background work. Interactive add-show / add-movie jobs are not listed.
        {!snap.schedules_loaded &&
          " Live schedule rows were unavailable; showing configured crons."}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <StatCard title="Background queue" footer="Waiting messages">
          {snap.queue_background == null ? "—" : snap.queue_background}
        </StatCard>
        <StatCard title="Interactive queue" footer="Waiting messages">
          {snap.queue_interactive == null ? "—" : snap.queue_interactive}
        </StatCard>
      </div>
      <DataList<DiagnosticsScheduledTask>
        data={snap.tasks}
        getId={(row) => row.task_name}
        columns={columns}
        disableSelection
        urlSync={false}
        pageSize={0}
        searchPlaceholder="Search tasks…"
        searchMatch={(row, q) =>
          row.display_name.toLowerCase().includes(q.toLowerCase()) ||
          row.task_name.toLowerCase().includes(q.toLowerCase())
        }
        emptyIcon={<Clock />}
        emptyTitle="No scheduled tasks"
        emptyDescription="The scheduler catalog is empty."
      />
    </div>
  );
}
