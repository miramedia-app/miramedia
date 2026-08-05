"use client";

import * as React from "react";
import {
  EllipsisVertical,
  FlaskConical,
  Link as LinkIcon,
  LoaderCircle,
  Pencil,
  Power,
  PowerOff,
  Shield,
  Trash2,
} from "lucide-react";

import { StatusPill } from "@/components/ui/status-pill";
import { TypePill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type {
  BulkAction,
  ColumnDef,
  FacetDef,
  GroupByDef,
  SortOption,
} from "@/components/data-list";
import apiClient from "@/lib/api/client";
import { bulkMutate } from "@/lib/bulk-mutate";
import { toast } from "sonner";
import {
  indexerSearchMatch,
  siteHealthGroup,
  sitePriority,
  siteTestFacetValue,
  siteTypeLabel,
} from "@/lib/indexers";
import type { Site } from "@/lib/indexers";

export { indexerSearchMatch };

export interface BuildIndexerColumnsOptions {
  updateSite: (siteId: string, body: Record<string, unknown>) => void;
  openUrls: (site: Site) => void;
}

export function buildIndexerColumns({
  updateSite,
  openUrls,
}: BuildIndexerColumnsOptions): ColumnDef<Site>[] {
  return [
    {
      id: "name",
      header: "Name",
      width: "minmax(120px,0.5fr)",
      render: (s) => (
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-medium">{s.name}</span>
          {s.cloudflare_protected && <Shield className="h-3.5 w-3.5 shrink-0 text-orange-500" />}
        </div>
      ),
    },
    {
      id: "url",
      header: "URL",
      width: "minmax(0,1fr)",
      render: (s) => (
        <div className="flex min-w-0 items-center gap-1">
          <span className="truncate text-xs text-muted-foreground">{s.url}</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-5 w-5 shrink-0 p-0 text-muted-foreground hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              openUrls(s);
            }}
            title={(s.available_urls ?? []).length > 1 ? "Manage mirrors" : "Add mirror"}
          >
            <LinkIcon className="h-3 w-3" />
          </Button>
        </div>
      ),
    },
    {
      id: "type",
      header: "Type",
      width: "80px",
      render: (s) => <TypePill>{siteTypeLabel[s.site_type] ?? s.site_type}</TypePill>,
    },
    {
      id: "priority",
      header: "Priority",
      width: "80px",
      align: "center",
      hideBelow: "sm",
      render: (s) => (
        <span className="text-sm text-muted-foreground tabular-nums">{sitePriority(s)}</span>
      ),
    },
    {
      id: "supports",
      header: "Supports",
      width: "136px",
      hideBelow: "md",
      render: (s) => (
        <div className="grid w-full grid-cols-[60px_64px] items-center gap-1">
          {s.supports_tv ? (
            <TypePill className="justify-center">Shows</TypePill>
          ) : (
            <span aria-hidden />
          )}
          {s.supports_movies ? (
            <TypePill className="justify-center">Movies</TypePill>
          ) : (
            <span aria-hidden />
          )}
        </div>
      ),
    },
    {
      id: "health",
      header: "Health",
      width: "100px",
      align: "start",
      render: (s) => {
        if (s.last_test_status === "error") {
          return (
            <StatusPill
              status="failed"
              label="Failed"
              title={s.last_test_at ? new Date(s.last_test_at).toLocaleString() : undefined}
            />
          );
        }
        if (s.last_success_at) {
          return (
            <StatusPill
              status="healthy"
              label="Healthy"
              title={`Last OK ${new Date(s.last_success_at).toLocaleString()}`}
            />
          );
        }
        return null;
      },
    },
    {
      id: "status",
      header: "Status",
      width: "112px",
      align: "start",
      render: (s) => (
        <StatusPill
          status={s.enabled ? "enabled" : "disabled"}
          className="cursor-pointer"
          title="Toggle enabled"
          onClick={(e) => {
            e.stopPropagation();
            void updateSite(s.id, { enabled: !s.enabled });
          }}
        />
      ),
    },
  ];
}

export const INDEXER_FACETS: FacetDef<Site>[] = [
  {
    id: "type",
    label: "Type",
    options: [
      { value: "native", label: "System" },
      { value: "torznab", label: "Custom" },
    ],
    predicate: (s, values, op) => {
      const hit = values.includes(s.site_type);
      return op === "excludes" ? !hit : hit;
    },
  },
  {
    id: "enabled",
    label: "Enabled",
    options: [
      { value: "yes", label: "Enabled" },
      { value: "no", label: "Disabled" },
    ],
    predicate: (s, values, op) => {
      const hit = values.includes(s.enabled ? "yes" : "no");
      return op === "excludes" ? !hit : hit;
    },
  },
  {
    id: "supports",
    label: "Supports",
    options: [
      { value: "shows", label: "Shows" },
      { value: "movies", label: "Movies" },
    ],
    predicate: (s, values, op) => {
      const hit = values.some(
        (v) => (v === "shows" && s.supports_tv) || (v === "movies" && s.supports_movies),
      );
      return op === "excludes" ? !hit : hit;
    },
  },
  {
    id: "test",
    label: "Test status",
    options: [
      { value: "error", label: "Failed" },
      { value: "ok", label: "OK / unknown" },
    ],
    predicate: (s, values, op) => {
      const hit = values.includes(siteTestFacetValue(s));
      return op === "excludes" ? !hit : hit;
    },
  },
];

export const INDEXER_SORT_OPTIONS: SortOption<Site>[] = [
  { id: "name-asc", label: "Name A–Z", compare: (a, b) => a.name.localeCompare(b.name) },
  { id: "name-desc", label: "Name Z–A", compare: (a, b) => b.name.localeCompare(a.name) },
  {
    id: "priority-asc",
    label: "Priority (low first)",
    compare: (a, b) => sitePriority(a) - sitePriority(b),
  },
  {
    id: "last-success-desc",
    label: "Last success (newest)",
    compare: (a, b) =>
      new Date(b.last_success_at ?? 0).getTime() - new Date(a.last_success_at ?? 0).getTime(),
  },
];

export const INDEXER_GROUPINGS: GroupByDef<Site>[] = [
  {
    id: "type",
    label: "Type",
    getGroup: (s) => ({
      key: s.site_type,
      label: siteTypeLabel[s.site_type] ?? s.site_type,
      sortOrder: s.site_type === "native" ? 0 : 1,
    }),
  },
  {
    id: "status",
    label: "Status",
    getGroup: (s) => ({
      key: s.enabled ? "enabled" : "disabled",
      label: s.enabled ? "Enabled" : "Disabled",
      sortOrder: s.enabled ? 0 : 1,
    }),
  },
  {
    id: "health",
    label: "Health",
    getGroup: (s) => siteHealthGroup(s),
  },
];

/** Bulk enable/disable actions. `invalidateSites` refetches after each batch. */
export function buildIndexerBulkActions(
  invalidateSites: () => Promise<void> | void,
): BulkAction<Site>[] {
  const run = async (items: Site[], enabled: boolean) => {
    const { ok, failed } = await bulkMutate(items, (s) =>
      apiClient.PUT("/api/v1/indexers/sites/{site_id}", {
        params: { path: { site_id: s.id } },
        body: { enabled } as never,
      }),
    );
    await invalidateSites();
    const verb = enabled ? "Enabled" : "Disabled";
    const lower = enabled ? "enable" : "disable";
    if (failed === 0) {
      toast.success(`${verb} ${ok} site(s)`);
    } else if (ok === 0) {
      toast.error(`Failed to ${lower} ${failed} site(s)`);
    } else {
      toast.warning(`${verb} ${ok} site(s), ${failed} failed`);
    }
  };
  return [
    {
      id: "enable",
      label: "Enable",
      icon: <Power className="h-3.5 w-3.5" />,
      variant: "secondary",
      onRun: (items) => run(items, true),
    },
    {
      id: "disable",
      label: "Disable",
      icon: <PowerOff className="h-3.5 w-3.5" />,
      variant: "secondary",
      onRun: (items) => run(items, false),
    },
  ];
}

export interface IndexerRowActionsProps {
  site: Site;
  testingId: string | null;
  onTest: (site: Site) => void;
  onEdit: (site: Site) => void;
  onManageUrls: (site: Site) => void;
  onDelete: (siteId: string, siteName: string) => void;
}

/** Test / edit / overflow (manage URLs, delete) actions for one indexer row. */
export function IndexerRowActions({
  site,
  testingId,
  onTest,
  onEdit,
  onManageUrls,
  onDelete,
}: IndexerRowActionsProps) {
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 text-muted-foreground"
        disabled={testingId === site.id}
        onClick={(e) => {
          e.stopPropagation();
          onTest(site);
        }}
        title="Test"
      >
        {testingId === site.id ? (
          <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <FlaskConical className="h-3.5 w-3.5" />
        )}
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 text-muted-foreground"
        onClick={(e) => {
          e.stopPropagation();
          onEdit(site);
        }}
        title="Edit"
      >
        <Pencil className="h-3.5 w-3.5" />
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
              <EllipsisVertical className="h-4 w-4" />
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => onManageUrls(site)}>
            <LinkIcon className="mr-2 h-4 w-4" />
            Manage URLs
          </DropdownMenuItem>
          {!site.is_preloaded && (
            <DropdownMenuItem
              className="text-destructive"
              onClick={() => onDelete(site.id, site.name)}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Delete
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
