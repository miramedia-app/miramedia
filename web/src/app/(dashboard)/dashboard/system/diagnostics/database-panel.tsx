"use client";

import { useQuery } from "@tanstack/react-query";
import { Database, LoaderCircle } from "lucide-react";

import { DataList } from "@/components/data-list";
import type { ColumnDef } from "@/components/data-list";
import { StatCard } from "@/components/stats/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";
import { DIAGNOSTICS_ERROR_MESSAGE, formatBytes } from "@/lib/diagnostics";
import { qk } from "@/lib/query-keys";

type DiagnosticsDatabase = components["schemas"]["DiagnosticsDatabase"];
type DiagnosticsDatabaseTable = components["schemas"]["DiagnosticsDatabaseTable"];

function formatStartedAt(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export function DiagnosticsDatabasePanel({ enabled }: { enabled: boolean }) {
  const query = useQuery({
    queryKey: qk.diagnostics.database(),
    enabled,
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/diagnostics/database", { signal });
      if (error) throw error;
      return data as DiagnosticsDatabase;
    },
  });

  const columns: ColumnDef<DiagnosticsDatabaseTable>[] = [
    {
      id: "name",
      header: "Table",
      width: "minmax(0,1fr)",
      mobile: { role: "title" },
      render: (row) => <span className="font-mono text-sm">{row.name}</span>,
    },
    {
      id: "total",
      header: "Total",
      width: "96px",
      align: "end",
      mono: true,
      mobile: { role: "meta", order: 0 },
      render: (row) => formatBytes(row.total_bytes),
    },
    {
      id: "heap",
      header: "Data",
      width: "88px",
      align: "end",
      hideBelow: "md",
      mono: true,
      mobile: { role: "hidden" },
      render: (row) => formatBytes(row.table_bytes),
    },
    {
      id: "indexes",
      header: "Indexes",
      width: "88px",
      align: "end",
      hideBelow: "md",
      mono: true,
      mobile: { role: "hidden" },
      render: (row) => formatBytes(row.index_bytes),
    },
    {
      id: "rows",
      header: "Rows",
      width: "88px",
      align: "end",
      mono: true,
      mobile: { role: "meta", order: 1 },
      render: (row) => (row.estimated_rows == null ? "—" : row.estimated_rows.toLocaleString()),
    },
  ];

  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Failed to load database</AlertTitle>
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
        Loading database…
      </div>
    );
  }

  const snap = query.data;
  const connectionTotal = snap.connections.reduce((sum, row) => sum + row.count, 0);
  const requestPool = snap.pools.find((pool) => pool.name === "request");
  const backgroundPool = snap.pools.find((pool) => pool.name === "background");

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {snap.user}@{snap.host}:{snap.port}/{snap.name}
        {snap.server_version ? ` · PostgreSQL ${snap.server_version}` : ""}
      </p>
      {snap.started_at && (
        <p className="text-xs text-muted-foreground">
          Server started {formatStartedAt(snap.started_at)}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard
          title="Database size"
          footer={snap.max_connections ? `${snap.max_connections} max connections` : "On-disk size"}
        >
          {formatBytes(snap.size_bytes)}
        </StatCard>
        <StatCard
          title="Connections"
          footer={
            snap.connections.length > 0
              ? snap.connections.map((row) => `${row.count} ${row.state}`).join(" · ")
              : "No activity snapshot"
          }
        >
          {connectionTotal}
        </StatCard>
        <StatCard
          title="Request pool"
          footer={
            requestPool
              ? `${requestPool.checked_out ?? "—"} checked out · overflow ${requestPool.overflow ?? "—"}`
              : "Engine not initialized"
          }
        >
          {requestPool?.size ?? "—"}
        </StatCard>
        <StatCard
          title="Background pool"
          footer={
            backgroundPool
              ? `${backgroundPool.checked_out ?? "—"} checked out · overflow ${backgroundPool.overflow ?? "—"}`
              : "Engine not initialized"
          }
        >
          {backgroundPool?.size ?? "—"}
        </StatCard>
      </div>

      <DataList<DiagnosticsDatabaseTable>
        data={snap.largest_tables}
        getId={(row) => row.name}
        columns={columns}
        disableSelection
        urlSync={false}
        pageSize={0}
        searchPlaceholder="Search tables…"
        searchMatch={(row, q) => row.name.toLowerCase().includes(q.toLowerCase())}
        emptyIcon={<Database />}
        emptyTitle="No table sizes"
        emptyDescription="Table sizes are unavailable until PostgreSQL catalog queries succeed."
      />
    </div>
  );
}
