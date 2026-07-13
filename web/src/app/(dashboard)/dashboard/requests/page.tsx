"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, EllipsisVertical, Inbox, Trash2, X } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DashboardHeader } from "@/components/dashboard-header";
import { StatusPill } from "@/components/ui/status-pill";
import { MetaPill, TypePill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import { DataList } from "@/components/data-list";
import type {
  BulkAction,
  ColumnDef,
  FacetDef,
  GroupByDef,
  SortOption,
} from "@/components/data-list";
import { useUser } from "@/components/providers/user-provider";
import apiClient from "@/lib/api/client";
import { pLimit } from "@/lib/p-limit";
import type { components } from "@/lib/api/api";

type MediaRequest = components["schemas"]["MediaRequest"];

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function qualityLabel(q: number | null | undefined): string {
  if (q == null) return "Default";
  switch (q) {
    case 1:
      return "4K";
    case 2:
      return "1080p";
    case 3:
      return "720p";
    case 4:
      return "SD";
    default:
      return "Unknown";
  }
}

const STATUS_ORDER: Record<string, number> = {
  pending: 0,
  approved: 1,
  downloading: 2,
  downloaded: 3,
  rejected: 4,
};

export default function RequestsPage() {
  const { user } = useUser();
  const qc = useQueryClient();

  const requestsQuery = useQuery({
    queryKey: ["requests", "list"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/requests", {
        params: { query: {} },
      });
      if (error) throw error;
      return (data ?? []) as MediaRequest[];
    },
  });

  const requests = requestsQuery.data ?? [];

  async function approveRequest(id: string) {
    const { error } = await apiClient.PATCH("/api/v1/requests/{request_id}/approve", {
      params: { path: { request_id: id } },
    });
    if (!error) {
      toast.success("Request approved");
      await qc.invalidateQueries({ queryKey: ["requests"] });
    } else {
      toast.error("Failed to approve request");
    }
  }

  async function rejectRequest(id: string) {
    const { error } = await apiClient.PATCH("/api/v1/requests/{request_id}/reject", {
      params: { path: { request_id: id } },
    });
    if (!error) {
      toast.success("Request rejected");
      await qc.invalidateQueries({ queryKey: ["requests"] });
    } else {
      toast.error("Failed to reject request");
    }
  }

  async function deleteRequest(id: string) {
    const { response } = await apiClient.DELETE("/api/v1/requests/{request_id}", {
      params: { path: { request_id: id } },
    });
    if (response.ok) {
      toast.success("Request deleted");
      await qc.invalidateQueries({ queryKey: ["requests"] });
    } else {
      toast.error("Failed to delete request");
    }
  }

  const bulkRun = React.useCallback(
    async (items: MediaRequest[], action: "approve" | "reject" | "delete") => {
      const ids = items.map((r) => r.id!).filter(Boolean);
      if (!ids.length) return;
      try {
        // Cap concurrency so "select all" doesn't flood the backend with
        // hundreds of in-flight mutations.
        await pLimit<string, unknown>(8, ids, (id) => {
          if (action === "approve") {
            return apiClient.PATCH("/api/v1/requests/{request_id}/approve", {
              params: { path: { request_id: id } },
            });
          }
          if (action === "reject") {
            return apiClient.PATCH("/api/v1/requests/{request_id}/reject", {
              params: { path: { request_id: id } },
            });
          }
          return apiClient.DELETE("/api/v1/requests/{request_id}", {
            params: { path: { request_id: id } },
          });
        });
        toast.success(
          action === "approve"
            ? `${ids.length} request${ids.length !== 1 ? "s" : ""} approved`
            : action === "reject"
              ? `${ids.length} request${ids.length !== 1 ? "s" : ""} rejected`
              : `${ids.length} request${ids.length !== 1 ? "s" : ""} deleted`,
        );
        await qc.invalidateQueries({ queryKey: ["requests"] });
      } catch {
        toast.error("Failed to apply bulk action.");
      }
    },
    [qc],
  );

  const columns = React.useMemo<ColumnDef<MediaRequest>[]>(
    () => [
      {
        id: "title",
        header: "Title",
        width: "minmax(0,1fr)",
        render: (r) => <span className="truncate text-sm font-medium">{r.title}</span>,
      },
      {
        id: "type",
        header: "Type",
        width: "72px",
        render: (r) => <TypePill>{r.media_type === "show" ? "Show" : "Movie"}</TypePill>,
      },
      {
        id: "quality",
        header: "Quality",
        width: "88px",
        hideBelow: "sm",
        render: (r) => <MetaPill className="font-mono">{qualityLabel(r.wanted_quality)}</MetaPill>,
      },
      {
        id: "requested_by",
        header: "Requested by",
        width: "160px",
        hideBelow: "lg",
        render: (r) => (
          <span
            className="truncate text-xs text-muted-foreground"
            title={r.requested_by_username ?? undefined}
          >
            {r.requested_by_username ?? "—"}
          </span>
        ),
      },
      {
        id: "created",
        header: "Created",
        width: "100px",
        hideBelow: "md",
        mono: true,
        render: (r) => (
          <span className="text-xs text-muted-foreground">
            {r.created_at ? new Date(r.created_at).toLocaleDateString() : "—"}
          </span>
        ),
      },
      {
        id: "note",
        header: "Note",
        width: "200px",
        hideBelow: "lg",
        render: (r) =>
          r.note ? (
            <span className="block truncate pr-4 text-xs text-muted-foreground" title={r.note}>
              {r.note}
            </span>
          ) : null,
      },
      {
        id: "status",
        header: "Status",
        width: "112px",
        render: (r) => <StatusPill status={r.status} />,
      },
    ],
    [],
  );

  const facets = React.useMemo<FacetDef<MediaRequest>[]>(
    () => [
      {
        id: "type",
        label: "Type",
        options: [
          { value: "show", label: "Show" },
          { value: "movie", label: "Movie" },
        ],
        predicate: (r, values, op) => {
          const hit = values.includes(r.media_type);
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "status",
        label: "Status",
        options: [
          { value: "pending", label: "Pending" },
          { value: "approved", label: "Approved" },
          { value: "downloading", label: "Downloading" },
          { value: "downloaded", label: "Downloaded" },
          { value: "rejected", label: "Rejected" },
        ],
        predicate: (r, values, op) => {
          const hit = values.includes(r.status);
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "quality",
        label: "Quality",
        options: [
          { value: "4K", label: "4K" },
          { value: "1080p", label: "1080p" },
          { value: "720p", label: "720p" },
          { value: "SD", label: "SD" },
          { value: "Default", label: "Default" },
        ],
        predicate: (r, values, op) => {
          const hit = values.includes(qualityLabel(r.wanted_quality));
          return op === "excludes" ? !hit : hit;
        },
      },
    ],
    [],
  );

  const sortOptions = React.useMemo<SortOption<MediaRequest>[]>(
    () => [
      {
        id: "newest",
        label: "Newest first",
        compare: (a, b) =>
          new Date(b.created_at ?? "").getTime() - new Date(a.created_at ?? "").getTime(),
      },
      {
        id: "oldest",
        label: "Oldest first",
        compare: (a, b) =>
          new Date(a.created_at ?? "").getTime() - new Date(b.created_at ?? "").getTime(),
      },
      { id: "title-asc", label: "Title A–Z", compare: (a, b) => a.title.localeCompare(b.title) },
      { id: "title-desc", label: "Title Z–A", compare: (a, b) => b.title.localeCompare(a.title) },
    ],
    [],
  );

  const groupings = React.useMemo<GroupByDef<MediaRequest>[]>(
    () => [
      {
        id: "status",
        label: "Status",
        getGroup: (r) => ({
          key: r.status,
          label: capitalize(r.status),
          sortOrder: STATUS_ORDER[r.status] ?? 99,
        }),
      },
      {
        id: "type",
        label: "Type",
        getGroup: (r) => ({
          key: r.media_type,
          label: r.media_type === "show" ? "Shows" : "Movies",
        }),
      },
    ],
    [],
  );

  const bulkActions = React.useMemo<BulkAction<MediaRequest>[]>(
    () =>
      user?.is_superuser
        ? [
            {
              id: "approve",
              label: "Approve",
              icon: <Check className="h-3.5 w-3.5" />,
              variant: "secondary",
              onRun: (items) =>
                bulkRun(
                  items.filter((r) => r.status === "pending"),
                  "approve",
                ),
            },
            {
              id: "reject",
              label: "Reject",
              icon: <X className="h-3.5 w-3.5" />,
              variant: "secondary",
              onRun: (items) =>
                bulkRun(
                  items.filter((r) => r.status === "pending"),
                  "reject",
                ),
            },
            {
              id: "delete",
              label: "Delete",
              icon: <Trash2 className="h-3.5 w-3.5" />,
              variant: "destructive",
              onRun: (items) => bulkRun(items, "delete"),
            },
          ]
        : [],
    [user?.is_superuser, bulkRun],
  );

  const renderRowActions = React.useCallback(
    (r: MediaRequest) =>
      user?.is_superuser ? (
        <>
          {r.status === "pending" && (
            <>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-green-600"
                title="Approve"
                onClick={() => void approveRequest(r.id ?? "")}
              >
                <Check className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-destructive"
                title="Reject"
                onClick={() => void rejectRequest(r.id ?? "")}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                  <EllipsisVertical className="h-4 w-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                className="text-destructive"
                onClick={() => void deleteRequest(r.id ?? "")}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      ) : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [user?.is_superuser],
  );

  return (
    <>
      <DashboardHeader
        crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Requests" }]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        <DataList<MediaRequest>
          data={requests}
          getId={(r) => r.id!}
          columns={columns}
          searchPlaceholder="Search or filter requests…"
          searchMatch={(r, q) => r.title.toLowerCase().includes(q)}
          facets={facets}
          sortOptions={sortOptions}
          defaultSort="newest"
          groupings={groupings}
          defaultGroupId="status"
          collapseStorageKey="requests"
          bulkActions={bulkActions}
          loading={requestsQuery.isLoading}
          emptyIcon={<Inbox />}
          emptyTitle="No requests yet"
          emptyDescription="Requests will appear here when users submit them."
          rowActions={renderRowActions}
        />
      </main>
    </>
  );
}
