"use client";

import * as React from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Check,
  SlidersHorizontal,
  LoaderCircle,
  RefreshCw,
  Trash2,
  Download,
  Play,
  Pause,
  Info,
  AlertTriangle,
  AlertOctagon,
  Bug,
  FileText,
  Copy,
  Layers,
} from "lucide-react";
import { copyToClipboard } from "@/lib/utils";
import { DashboardHeader } from "@/components/dashboard-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { statusVariant } from "@/components/ui/status-pill";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MediaPagination } from "@/components/media-pagination";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import {
  DataListSearchFilter,
  DataListSection,
  DataListHeaderRow,
  DataListGroupHeader,
  useCollapsedGroups,
  DataListSkeleton,
  DataListEmpty,
} from "@/components/data-list";
import type { ColumnDef, FacetDef } from "@/components/data-list";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type ActivityLogRead = components["schemas"]["ActivityLogRead"];

const getLogEntryId = (entry: ActivityLogRead) => entry.id;

const LEVEL_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  CRITICAL: AlertOctagon,
  ERROR: AlertOctagon,
  WARNING: AlertTriangle,
  DEBUG: Bug,
  INFO: Info,
};

const GROUP_OPTIONS = [
  { id: "level", label: "Level" },
  { id: "module", label: "Module" },
] as const;

/** Short relative time ("3m", "2h", "5d"); falls back to the date. */
function relativeTime(iso: string | undefined, now = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d}d`;
  return new Date(iso).toLocaleDateString();
}

/** Trim the shared package prefix so the logger name fits one line. */
function shortModule(module: string | undefined): string {
  if (!module) return "";
  return module.startsWith("miramedia.") ? module.slice("miramedia.".length) : module;
}

function LevelBadge({ level }: { level?: string }) {
  const LevelIcon = level ? (LEVEL_ICONS[level.toUpperCase()] ?? FileText) : FileText;
  return (
    <Badge
      variant={statusVariant(level ?? "unknown")}
      className={`h-5 shrink-0 gap-1 px-1.5 text-[11px] ${levelTextColor(level)}`}
    >
      <LevelIcon className="h-3 w-3" />
      {level ?? ""}
    </Badge>
  );
}

/**
 * Phone card for one log entry: level pill + logger + relative time on the
 * first line, message clamped to three lines; tapping expands the full
 * message, timestamp, correlation id and extra fields.
 */
function LogCard({
  entry,
  expanded,
  onToggle,
  onModuleFilter,
}: {
  entry: ActivityLogRead;
  expanded: boolean;
  onToggle: () => void;
  onModuleFilter: (module: string) => void;
}) {
  const extra = entry.extra && Object.keys(entry.extra).length > 0 ? entry.extra : null;
  return (
    <div className="border-b border-border/50 last:border-b-0" data-slot="log-card">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className={cn(
          "flex min-h-14 w-full flex-col gap-1 px-4 py-2.5 text-left transition-colors active:bg-muted/50",
          expanded && "bg-muted/30",
        )}
      >
        <div className="flex w-full min-w-0 items-center gap-2">
          <LevelBadge level={entry.level} />
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
            {shortModule(entry.module)}
          </span>
          <time
            dateTime={entry.timestamp}
            className="shrink-0 text-[11px] text-muted-foreground tabular-nums"
          >
            {relativeTime(entry.timestamp)}
          </time>
        </div>
        <p
          className={cn(
            "w-full font-mono text-xs leading-relaxed break-words",
            expanded ? "whitespace-pre-wrap" : "line-clamp-3",
          )}
        >
          {entry.message ?? ""}
        </p>
      </button>
      {expanded && (
        <div className="flex flex-col gap-2 px-4 pb-3 font-mono text-[11px] text-muted-foreground">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 break-all">
            <dt>time</dt>
            <dd className="text-foreground tabular-nums">{entry.timestamp}</dd>
            <dt>module</dt>
            <dd>
              <button
                type="button"
                className="text-foreground underline-offset-2 hover:underline"
                onClick={() => entry.module && onModuleFilter(entry.module)}
              >
                {entry.module}
              </button>
            </dd>
            {entry.correlation_id && (
              <>
                <dt>corr</dt>
                <dd className="text-foreground">{entry.correlation_id}</dd>
              </>
            )}
          </dl>
          {extra && (
            <pre className="max-h-56 overflow-auto rounded border bg-background p-2 break-all whitespace-pre-wrap text-foreground">
              {JSON.stringify(extra, null, 2)}
            </pre>
          )}
          <Button
            variant="outline"
            size="sm"
            className="self-start text-xs"
            onClick={() => {
              copyToClipboard(entry.message ?? "")
                .then(() => toast.success("Message copied"))
                .catch(() => toast.error("Copy failed"));
            }}
          >
            <Copy className="mr-1 h-3.5 w-3.5" />
            Copy message
          </Button>
        </div>
      )}
    </div>
  );
}

function LogCardList({
  entries,
  onModuleFilter,
}: {
  entries: ActivityLogRead[];
  onModuleFilter: (module: string) => void;
}) {
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <div className="flex flex-col">
      {entries.map((entry) => (
        <LogCard
          key={entry.id}
          entry={entry}
          expanded={expanded.has(entry.id)}
          onToggle={() => toggle(entry.id)}
          onModuleFilter={onModuleFilter}
        />
      ))}
    </div>
  );
}

function DrawerRadioList({
  title,
  options,
  value,
  onChange,
}: {
  title: string;
  options: { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label={title} className="flex flex-col gap-1">
      <div className="px-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </div>
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.id)}
            className={cn(
              "flex min-h-11 items-center justify-between rounded-md px-3 text-left text-sm",
              active ? "bg-accent text-accent-foreground" : "hover:bg-muted",
            )}
          >
            {o.label}
            {active && <Check className="h-4 w-4" />}
          </button>
        );
      })}
    </div>
  );
}

function levelTextColor(level?: string): string {
  switch (level?.toUpperCase()) {
    case "ERROR":
    case "CRITICAL":
      return "text-red-500";
    case "WARNING":
      return "text-yellow-500";
    case "DEBUG":
      return "text-muted-foreground";
    default:
      return "";
  }
}

function LogsPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  const level = searchParams.get("level") ?? "";
  const moduleFilter = searchParams.get("module") ?? "";
  const search = searchParams.get("search") ?? "";
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);
  const limit = parseInt(searchParams.get("limit") ?? "50", 10);
  const group = searchParams.get("group") ?? "none";

  const [tailing, setTailing] = React.useState(false);

  const logsQuery = useQuery({
    queryKey: ["system", "logs", level, moduleFilter, search, offset, limit],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/system/logs", {
        signal,
        params: {
          query: {
            offset,
            limit,
            ...(level ? { level } : {}),
            ...(moduleFilter ? { module: moduleFilter } : {}),
            ...(search ? { search } : {}),
          },
        },
      });
      if (error) throw error;
      return data ?? null;
    },
    refetchInterval: tailing ? 3000 : false,
    refetchIntervalInBackground: false,
  });

  const items = logsQuery.data?.items ?? [];
  const total = logsQuery.data?.total ?? 0;

  function updateParams(updates: Record<string, string | undefined>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    router.push(`${pathname}?${params.toString()}`);
  }

  const [searchInput, setSearchInput] = React.useState(search);
  React.useEffect(() => setSearchInput(search), [search]);
  React.useEffect(() => {
    const timer = setTimeout(() => {
      if (searchInput !== search) {
        updateParams({ search: searchInput || undefined, offset: undefined });
      }
    }, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  const levelOptions = React.useMemo(
    () => [
      { value: "INFO", label: "Info" },
      { value: "WARNING", label: "Warning" },
      { value: "ERROR", label: "Error" },
      { value: "CRITICAL", label: "Critical" },
      { value: "DEBUG", label: "Debug" },
    ],
    [],
  );

  const moduleOptions = React.useMemo(() => {
    const modules = new Set<string>();
    for (const item of logsQuery.data?.items ?? []) {
      if (item.module) modules.add(item.module);
    }
    if (moduleFilter) modules.add(moduleFilter);
    return Array.from(modules)
      .sort()
      .map((m) => ({ value: m, label: m }));
  }, [logsQuery.data, moduleFilter]);

  const facets: FacetDef<ActivityLogRead>[] = React.useMemo(
    () => [
      {
        id: "level",
        label: "Level",
        options: levelOptions,
        defaultOperator: "includes",
        operators: ["includes"],
        predicate: () => true,
      },
      {
        id: "module",
        label: "Module",
        options: moduleOptions,
        defaultOperator: "includes",
        operators: ["includes"],
        predicate: () => true,
      },
    ],
    [levelOptions, moduleOptions],
  );

  const activeFilters = React.useMemo(() => {
    const filters = [];
    if (level) {
      filters.push({ facetId: "level", operator: "includes" as const, values: [level] });
    }
    if (moduleFilter) {
      filters.push({ facetId: "module", operator: "includes" as const, values: [moduleFilter] });
    }
    return filters;
  }, [level, moduleFilter]);

  const hasFilters = !!(level || moduleFilter || search);

  const [refreshing, setRefreshing] = React.useState(false);
  async function refresh() {
    setRefreshing(true);
    try {
      await queryClient.invalidateQueries({ queryKey: ["system", "logs"] });
    } finally {
      setRefreshing(false);
    }
  }

  React.useEffect(() => {
    if (tailing && offset !== 0) {
      updateParams({ offset: undefined });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tailing]);

  const [exporting, setExporting] = React.useState(false);
  async function exportLogs() {
    setExporting(true);
    try {
      const params = new URLSearchParams();
      if (level) params.set("level", level);
      if (moduleFilter) params.set("module", moduleFilter);
      if (search) params.set("search", search);

      // Same base-URL rule as the typed client and SSE: a bare relative path
      // hits the wrong origin in split-origin deploys.
      const base = process.env.NEXT_PUBLIC_API_URL || "";
      const response = await fetch(`${base}/api/v1/system/logs/export?${params.toString()}`, {
        credentials: "include",
      });
      if (!response.ok) {
        toast.error("Export failed");
        return;
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download =
        response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ??
        "miramedia-logs.ndjson";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      toast.success("Logs exported");
    } catch {
      toast.error("Export failed");
    } finally {
      setExporting(false);
    }
  }

  const [clearOpen, setClearOpen] = React.useState(false);
  const [clearing, setClearing] = React.useState(false);
  async function clearAllLogs() {
    setClearing(true);
    try {
      const { error } = await apiClient.DELETE("/api/v1/system/logs");
      if (error) {
        toast.error("Failed to clear logs");
        return;
      }
      toast.success("All logs cleared");
      setClearOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["system", "logs"] });
    } finally {
      setClearing(false);
    }
  }

  const columns: ColumnDef<ActivityLogRead>[] = [
    {
      id: "timestamp",
      header: "Timestamp",
      width: "180px",
      mono: true,
      mobile: { role: "subtitle" },
      render: (entry) => (
        <span className="text-xs text-muted-foreground">{entry.timestamp ?? ""}</span>
      ),
    },
    {
      id: "level",
      header: "Level",
      width: "110px",
      mobile: { role: "meta", order: 0 },
      render: (entry) => <LevelBadge level={entry.level} />,
    },
    {
      id: "module",
      header: "Module",
      width: "340px",
      hideBelow: "md",
      mobile: { role: "meta", order: 1 },
      render: (entry) => (
        <button
          type="button"
          className="truncate font-mono text-xs hover:underline"
          title="Filter by module"
          onClick={(event) => {
            event.stopPropagation();
            const mod = entry.module ?? "";
            if (mod) updateParams({ module: mod, offset: undefined });
          }}
        >
          {entry.module ?? ""}
        </button>
      ),
    },
    {
      id: "message",
      header: "Message",
      width: "minmax(0,1fr)",
      mobile: { role: "title" },
      render: (entry) => <span className="truncate font-mono text-xs">{entry.message ?? ""}</span>,
    },
  ];

  const { collapsed, toggle: toggleGroup } = useCollapsedGroups("logs-groups");
  const gridTracks = ["24px", ...columns.map((col) => col.width)];
  const gridTemplate = gridTracks.join(" ");
  const isMobile = useIsMobile();
  const mobileFilterCount = (level ? 1 : 0) + (group !== "none" ? 1 : 0);
  const filterByModule = (mod: string) => updateParams({ module: mod, offset: undefined });

  const groupedEntries = React.useMemo(() => {
    if (group === "none") return null;
    const groups = new Map<string, ActivityLogRead[]>();
    for (const entry of logsQuery.data?.items ?? []) {
      const key = group === "level" ? (entry.level ?? "UNKNOWN") : (entry.module ?? "—");
      const bucket = groups.get(key);
      if (bucket) bucket.push(entry);
      else groups.set(key, [entry]);
    }
    return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [logsQuery.data, group]);

  // Every log entry expands (the message pane is always rendered), so the
  // predicate is a constant — it just lets collapsed rows skip building it.
  const isLogExpandable = React.useCallback(() => true, []);

  function renderExpandedContent(entry: ActivityLogRead) {
    return (
      <div className="flex flex-col gap-3 bg-background py-3 pr-4 pl-11 font-mono text-xs">
        <div className="flex items-center gap-3">
          <pre className="max-h-64 min-w-0 flex-1 overflow-auto py-1 break-all whitespace-pre-wrap">
            {entry.message ?? ""}
          </pre>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 text-xs"
            onClick={(event) => {
              event.stopPropagation();
              copyToClipboard(entry.message ?? "")
                .then(() => toast.success("Message copied"))
                .catch(() => toast.error("Copy failed"));
            }}
          >
            <Copy className="mr-1 h-3.5 w-3.5" />
            Copy
          </Button>
        </div>
        {entry.extra && Object.keys(entry.extra).length > 0 && (
          <div className="flex flex-col gap-1">
            <span className="text-muted-foreground">
              <span className="text-foreground">Extra</span>
            </span>
            <pre className="max-h-48 overflow-auto rounded border p-3 break-all whitespace-pre-wrap">
              {JSON.stringify(entry.extra, null, 2)}
            </pre>
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "System", href: "/dashboard/system/users" },
          { label: "Logs" },
        ]}
      />

      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        <div className="flex flex-wrap items-center gap-2">
          <DataListSearchFilter
            search={searchInput}
            onSearchChange={setSearchInput}
            facets={facets}
            filters={activeFilters}
            onFiltersChange={(filters) => {
              updateParams({
                level: filters.find((f) => f.facetId === "level")?.values[0] || undefined,
                module: filters.find((f) => f.facetId === "module")?.values[0] || undefined,
                offset: undefined,
              });
            }}
            placeholder="Search or filter logs…"
            className="basis-full sm:basis-auto"
          />

          {isMobile ? (
            <Drawer>
              <DrawerTrigger asChild>
                <Button variant="outline" size="default" className="gap-1 text-xs">
                  <SlidersHorizontal className="h-4 w-4" />
                  Filters
                  {mobileFilterCount > 0 && (
                    <span className="rounded-full bg-primary px-1.5 text-[10px] leading-4 text-primary-foreground tabular-nums">
                      {mobileFilterCount}
                    </span>
                  )}
                </Button>
              </DrawerTrigger>
              <DrawerContent className="pb-safe-b">
                <DrawerHeader className="text-left">
                  <DrawerTitle>Filter logs</DrawerTitle>
                  <DrawerDescription className="sr-only">
                    Choose a minimum level and grouping
                  </DrawerDescription>
                </DrawerHeader>
                <div className="flex flex-col gap-5 overflow-y-auto px-4 pb-4">
                  <DrawerRadioList
                    title="Level"
                    options={[
                      { id: "", label: "All levels" },
                      ...levelOptions.map((o) => ({ id: o.value, label: o.label })),
                    ]}
                    value={level}
                    onChange={(id) => updateParams({ level: id || undefined, offset: undefined })}
                  />
                  <DrawerRadioList
                    title="Group by"
                    options={[{ id: "none", label: "None" }, ...GROUP_OPTIONS]}
                    value={group}
                    onChange={(id) => updateParams({ group: id === "none" ? undefined : id })}
                  />
                </div>
              </DrawerContent>
            </Drawer>
          ) : (
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button variant="outline" size="default" className="gap-1 text-xs">
                    <Layers className="h-4 w-4" />
                    {group !== "none"
                      ? (GROUP_OPTIONS.find((opt) => opt.id === group)?.label ?? "Group")
                      : "None"}
                  </Button>
                }
              />
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Group by</DropdownMenuLabel>
                  <DropdownMenuRadioGroup
                    value={group}
                    onValueChange={(value) =>
                      updateParams({ group: value === "none" ? undefined : value })
                    }
                  >
                    <DropdownMenuRadioItem value="none">None</DropdownMenuRadioItem>
                    <DropdownMenuSeparator />
                    {GROUP_OPTIONS.map((opt) => (
                      <DropdownMenuRadioItem key={opt.id} value={opt.id}>
                        {opt.label}
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          <span className="hidden h-6 w-px bg-border sm:block" />

          <Button
            variant={tailing ? "default" : "outline"}
            size="default"
            className="text-xs max-lg:w-11 max-lg:px-0"
            onClick={() => setTailing((prev) => !prev)}
            title={tailing ? "Stop following" : "Follow new logs (refresh every 3s)"}
          >
            {tailing ? (
              <>
                <Pause className="h-4 w-4 lg:mr-1" />
                <span className="max-lg:sr-only">Tailing</span>
              </>
            ) : (
              <>
                <Play className="h-4 w-4 lg:mr-1" />
                <span className="max-lg:sr-only">Tail</span>
              </>
            )}
          </Button>

          <Button
            variant="outline"
            size="default"
            className="text-xs max-lg:w-11 max-lg:px-0"
            onClick={() => void refresh()}
            disabled={refreshing}
          >
            {refreshing ? (
              <LoaderCircle className="h-4 w-4 animate-spin lg:mr-1" />
            ) : (
              <RefreshCw className="h-4 w-4 lg:mr-1" />
            )}
            <span className="max-lg:sr-only">Refresh</span>
          </Button>

          <Button
            variant="outline"
            size="default"
            className="text-xs max-lg:w-11 max-lg:px-0"
            onClick={() => void exportLogs()}
            disabled={exporting}
            title="Download filtered logs as NDJSON"
          >
            {exporting ? (
              <LoaderCircle className="h-4 w-4 animate-spin lg:mr-1" />
            ) : (
              <Download className="h-4 w-4 lg:mr-1" />
            )}
            <span className="max-lg:sr-only">Export</span>
          </Button>

          <Button
            variant="destructive"
            size="default"
            className="border-destructive/40 text-xs max-lg:w-11 max-lg:px-0"
            onClick={() => setClearOpen(true)}
            disabled={total === 0}
          >
            <Trash2 className="h-4 w-4 lg:mr-1" />
            <span className="max-lg:sr-only">Clear</span>
          </Button>
        </div>

        {logsQuery.isError && (
          <Alert variant="destructive">
            <AlertTitle>Failed to load logs</AlertTitle>
            <AlertDescription className="flex items-center gap-2">
              Showing last loaded results.
              <Button variant="outline" size="sm" onClick={() => logsQuery.refetch()}>
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {logsQuery.isLoading ? (
          <div className="overflow-hidden rounded-lg border bg-card">
            <DataListSkeleton rows={limit > 12 ? 12 : limit} density="compact" />
          </div>
        ) : items.length === 0 ? (
          hasFilters ? (
            <DataListEmpty
              icon={<FileText className="h-8 w-8" />}
              title="No matching logs"
              description="No log entries match your search or filters."
            />
          ) : (
            <DataListEmpty
              icon={<FileText className="h-8 w-8" />}
              title="No logs yet"
              description="Log entries will appear here as the system runs."
            />
          )
        ) : groupedEntries ? (
          <div className="overflow-hidden rounded-lg border bg-card">
            <div>
              {!isMobile && (
                <DataListHeaderRow
                  columns={columns}
                  gridTemplate={gridTemplate}
                  selectable={false}
                  hasExpandColumn
                  allSelected={false}
                  someSelected={false}
                  onToggleAll={() => {}}
                  hasRowActions={false}
                />
              )}
              {groupedEntries.map(([label, groupItems]) => {
                const isCollapsed = collapsed.has(label);
                return (
                  <React.Fragment key={label}>
                    <DataListGroupHeader
                      label={label}
                      count={groupItems.length}
                      collapsed={isCollapsed}
                      onToggle={() => toggleGroup(label)}
                      className={isMobile ? "top-0 h-10" : undefined}
                    />
                    {!isCollapsed && isMobile ? (
                      <LogCardList entries={groupItems} onModuleFilter={filterByModule} />
                    ) : null}
                    {!isCollapsed && !isMobile && (
                      <DataListSection
                        data={groupItems}
                        getId={getLogEntryId}
                        density="compact"
                        columns={columns}
                        showHeader={false}
                        bordered={false}
                        expandedContent={renderExpandedContent}
                        isExpandable={isLogExpandable}
                      />
                    )}
                  </React.Fragment>
                );
              })}
            </div>
          </div>
        ) : isMobile ? (
          <div className="overflow-hidden rounded-lg border bg-card">
            <LogCardList entries={items} onModuleFilter={filterByModule} />
          </div>
        ) : (
          <DataListSection
            data={items}
            getId={getLogEntryId}
            density="compact"
            columns={columns}
            expandedContent={renderExpandedContent}
            isExpandable={isLogExpandable}
          />
        )}

        <MediaPagination
          page={Math.floor(offset / limit) + 1}
          totalPages={Math.max(1, Math.ceil(total / limit))}
          onPageChange={(page) => {
            const nextOffset = (page - 1) * limit;
            updateParams({ offset: nextOffset > 0 ? String(nextOffset) : undefined });
          }}
          total={total}
          pageSize={limit}
          pageSizeOptions={[20, 50, 100, 200]}
          onPageSizeChange={(pageSize) =>
            updateParams({ limit: String(pageSize), offset: undefined })
          }
        />
      </main>

      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear all logs?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete all {total} log entries. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button variant="destructive" disabled={clearing} onClick={() => void clearAllLogs()}>
              {clearing ? "Clearing..." : "Clear All Logs"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export default function LogsPage() {
  return (
    <React.Suspense fallback={null}>
      <LogsPageInner />
    </React.Suspense>
  );
}
