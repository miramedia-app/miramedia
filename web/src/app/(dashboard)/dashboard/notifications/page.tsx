"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Bell, BellOff, Check, CheckCheck, Undo2 } from "lucide-react";
import { DashboardHeader } from "@/components/dashboard-header";
import { Button } from "@/components/ui/button";
import { DataList, DataListEmpty } from "@/components/data-list";
import type { BulkAction, ColumnDef, FacetDef, GroupByDef } from "@/components/data-list";
import { useFeatures, useFeaturesStatus } from "@/components/providers/features-provider";
import apiClient from "@/lib/api/client";
import { bulkMutate } from "@/lib/bulk-mutate";
import type { components } from "@/lib/api/api";

type Notification = components["schemas"]["Notification"];

export default function NotificationsPage() {
  const qc = useQueryClient();
  const { notifications: notificationsEnabled } = useFeatures();
  const { isError: featuresError } = useFeaturesStatus();

  const allQuery = useQuery({
    queryKey: ["notifications", "all"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/notifications", { signal });
      if (error) throw error;
      return (data ?? []) as Notification[];
    },
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    enabled: notificationsEnabled,
  });

  const notifications = allQuery.data ?? [];
  const unread = notifications.filter((n) => !n.read);

  const refresh = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["notifications"] });
  }, [qc]);

  async function markAsRead(id: string) {
    const { response } = await apiClient.PATCH("/api/v1/notifications/{notification_id}/read", {
      params: { path: { notification_id: id } },
    });
    if (response.ok) refresh();
  }

  async function markAsUnread(id: string) {
    const { response } = await apiClient.PATCH("/api/v1/notifications/{notification_id}/unread", {
      params: { path: { notification_id: id } },
    });
    if (response.ok) refresh();
  }

  const bulkMark = React.useCallback(
    async (items: Notification[], read: boolean) => {
      const targets = items.filter((n) => !!n.read !== read);
      if (targets.length === 0) return;
      const verbLabel = read ? "marked as read" : "marked as unread";
      // Cap concurrency so "mark all read" with 100+ items doesn't flood
      // the backend.
      const { ok, failed } = await bulkMutate(targets, (n) =>
        apiClient.PATCH(
          `/api/v1/notifications/{notification_id}/${read ? "read" : "unread"}` as
            | "/api/v1/notifications/{notification_id}/read"
            | "/api/v1/notifications/{notification_id}/unread",
          { params: { path: { notification_id: n.id! } } },
        ),
      );
      if (ok > 0) {
        toast.success(`${ok} marked as ${read ? "read" : "unread"}`);
      }
      if (failed > 0) {
        toast.error(`${failed} notification(s) could not be ${verbLabel}`);
      }
      refresh();
    },
    [refresh],
  );

  const columns = React.useMemo<ColumnDef<Notification>[]>(
    () => [
      {
        id: "state",
        header: "",
        width: "28px",
        render: (n) =>
          n.read ? (
            <span className="h-2 w-2 rounded-full bg-transparent" aria-hidden />
          ) : (
            <span className="h-2 w-2 rounded-full bg-primary" aria-label="Unread" />
          ),
      },
      {
        id: "message",
        header: "Message",
        width: "minmax(0,1fr)",
        render: (n) => (
          <div className="flex min-w-0 flex-col gap-0.5">
            <span
              className={`truncate text-sm ${n.read ? "text-muted-foreground" : "font-medium"}`}
            >
              {n.message}
            </span>
            <span className="truncate text-xs text-muted-foreground tabular-nums">
              {n.timestamp ? new Date(n.timestamp).toLocaleString() : "—"}
            </span>
          </div>
        ),
      },
    ],
    [],
  );

  const facets = React.useMemo<FacetDef<Notification>[]>(
    () => [
      {
        id: "state",
        label: "Status",
        options: [
          { value: "unread", label: "Unread" },
          { value: "read", label: "Read" },
        ],
        predicate: (n, values, op) => {
          const v = n.read ? "read" : "unread";
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
    ],
    [],
  );

  const groupings = React.useMemo<GroupByDef<Notification>[]>(
    () => [
      {
        id: "state",
        label: "Status",
        getGroup: (n) =>
          n.read
            ? { key: "read", label: "Read", sortOrder: 1 }
            : { key: "unread", label: "Unread", sortOrder: 0 },
      },
    ],
    [],
  );

  const bulkActions = React.useMemo<BulkAction<Notification>[]>(
    () => [
      {
        id: "read",
        label: "Mark read",
        icon: <Check className="h-3.5 w-3.5" />,
        variant: "secondary",
        onRun: (items) => bulkMark(items, true),
      },
      {
        id: "unread",
        label: "Mark unread",
        icon: <Undo2 className="h-3.5 w-3.5" />,
        variant: "secondary",
        onRun: (items) => bulkMark(items, false),
      },
    ],
    [bulkMark],
  );

  const renderRowActions = React.useCallback(
    (n: Notification) =>
      n.read ? (
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground"
          title="Mark as unread"
          onClick={() => void markAsUnread(n.id ?? "")}
        >
          <Undo2 className="h-3.5 w-3.5" />
        </Button>
      ) : (
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground"
          title="Mark as read"
          onClick={() => void markAsRead(n.id ?? "")}
        >
          <Check className="h-3.5 w-3.5" />
        </Button>
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  if (!notificationsEnabled) {
    return (
      <>
        <DashboardHeader
          crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Notifications" }]}
        />
        <main className="flex w-full flex-col gap-4 p-4 pt-0">
          <DataListEmpty
            icon={<BellOff />}
            title={featuresError ? "Features could not be loaded" : "Notifications disabled"}
            description={
              featuresError
                ? "The feature settings request failed. Check that the backend is reachable."
                : "Enable them in System → Settings → Notifications."
            }
          />
        </main>
      </>
    );
  }

  return (
    <>
      <DashboardHeader
        crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Notifications" }]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        <DataList<Notification>
          data={notifications}
          getId={(n) => n.id!}
          columns={columns}
          searchPlaceholder="Search or filter notifications…"
          searchMatch={(n, q) => (n.message ?? "").toLowerCase().includes(q)}
          facets={facets}
          groupings={groupings}
          defaultGroupId="state"
          collapseStorageKey="notifications"
          bulkActions={bulkActions}
          loading={allQuery.isLoading}
          emptyIcon={<Bell />}
          emptyTitle="All caught up"
          emptyDescription="No notifications."
          toolbarTrailing={
            unread.length > 0 ? (
              <Button
                size="default"
                className="text-xs"
                onClick={() => void bulkMark(notifications, true)}
              >
                <CheckCheck className="mr-1 h-4 w-4" />
                Mark all read
              </Button>
            ) : null
          }
          rowActions={renderRowActions}
        />
      </main>
    </>
  );
}
