"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { HardDrive, LoaderCircle } from "lucide-react";

import { DataList } from "@/components/data-list";
import type { ColumnDef, FacetDef } from "@/components/data-list";
import { StatCard } from "@/components/stats/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress, ProgressLabel } from "@/components/ui/progress";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { StatusPill } from "@/components/ui/status-pill";
import { TypePill, MetaPill } from "@/components/ui/type-pill";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";
import {
  STORAGE_HEALTH_COPY,
  STORAGE_HEALTH_ERROR_MESSAGE,
  formatBytes,
  parseStorageHealthSearch,
  storageHealthAllClear,
  storageHealthFilterParam,
  storageHealthImportsHref,
  storageHealthTitleHref,
  storageHealthUnknownHint,
  storageHealthViewState,
  volumeUsedPercent,
  type StorageHealthSqlState,
} from "@/lib/diagnostics";
import { qk } from "@/lib/query-keys";
import { qualityToString } from "@/lib/utils";

type StorageHealthFile = components["schemas"]["StorageHealthFile"];
type StorageHealthSummary = components["schemas"]["StorageHealthSummary"];

const STATE_LABELS: Record<StorageHealthFile["state"], string> = {
  corrupt: "Corrupt",
  unknown: "Not hashed",
  orphaned: "Orphaned",
  pending: "Pending",
  healthy: "Healthy",
  missing: "Missing",
  inaccessible: "Inaccessible",
};

const storageSearchMatch = (row: StorageHealthFile, q: string) =>
  (row.media_title ?? "").toLowerCase().includes(q) ||
  (row.episode ?? "").toLowerCase().includes(q);

export function DiagnosticsStoragePanel() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const parsed = parseStorageHealthSearch(searchParams);
  const [listPage, setListPage] = React.useState(() => ({
    page: parsed.page,
    pageSize: parsed.limit,
  }));
  const onPaginationChange = React.useCallback((next: { page: number; pageSize: number }) => {
    setListPage((prev) =>
      prev.page === next.page && prev.pageSize === next.pageSize ? prev : next,
    );
  }, []);
  React.useEffect(() => {
    setListPage((prev) => (prev.page === 1 ? prev : { ...prev, page: 1 }));
  }, [parsed.state, parsed.mediaType, parsed.q]);

  const summaryQuery = useQuery({
    queryKey: qk.diagnostics.storage.summary(),
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/diagnostics/storage", {
        signal,
      });
      if (error) throw error;
      return data as StorageHealthSummary;
    },
  });

  const listQuery = useQuery({
    queryKey: qk.diagnostics.storage.list({
      offset: (listPage.page - 1) * listPage.pageSize,
      limit: listPage.pageSize,
      state: parsed.state,
      mediaType: parsed.mediaType,
      q: parsed.q,
    }),
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/diagnostics/storage/files", {
        signal,
        params: {
          query: {
            offset: (listPage.page - 1) * listPage.pageSize,
            limit: listPage.pageSize,
            ...(parsed.state ? { state: parsed.state } : {}),
            ...(parsed.mediaType ? { media_type: parsed.mediaType } : {}),
            ...(parsed.q ? { q: parsed.q } : {}),
          },
        },
      });
      if (error) throw error;
      return data;
    },
  });

  // While a search is active, fetch the FULL list (all pages) so the DataList's
  // `searchMatch` filters across everything, not just the loaded server page.
  // NOTE: this 1B approach page-walks the whole (facet-scoped) list client-side
  // — the endpoint is capped at 100 rows/request. If the file count grows large,
  // switch to server-side `q` filtering (2B): the backend already accepts a `q`
  // query param on /api/v1/diagnostics/storage/files.
  const [searchQuery, setSearchQuery] = React.useState("");
  const searching = searchQuery.length > 0;
  const searchAllQuery = useQuery({
    queryKey: qk.diagnostics.storage.searchAll({
      state: parsed.state,
      mediaType: parsed.mediaType,
    }),
    enabled: searching,
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      const pageLimit = 100;
      const all: StorageHealthFile[] = [];
      for (let offset = 0; ; offset += pageLimit) {
        const { data, error } = await apiClient.GET("/api/v1/diagnostics/storage/files", {
          signal,
          params: {
            query: {
              offset,
              limit: pageLimit,
              ...(parsed.state ? { state: parsed.state } : {}),
              ...(parsed.mediaType ? { media_type: parsed.mediaType } : {}),
            },
          },
        });
        if (error) throw error;
        const items = data?.items ?? [];
        all.push(...items);
        if (items.length < pageLimit || all.length >= (data?.total ?? 0)) break;
      }
      return all;
    },
  });

  const detailQuery = useQuery({
    queryKey: qk.diagnostics.storage.detail(
      parsed.detailMediaType ?? "",
      parsed.detailFileId ?? "",
    ),
    enabled: Boolean(parsed.detailMediaType && parsed.detailFileId),
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET(
        "/api/v1/diagnostics/storage/files/{media_type}/{file_id}",
        {
          signal,
          params: {
            path: {
              media_type: parsed.detailMediaType!,
              file_id: parsed.detailFileId!,
            },
          },
        },
      );
      if (error) throw error;
      return data as StorageHealthFile;
    },
  });

  const replaceParams = React.useCallback(
    (mutate: (next: URLSearchParams) => void) => {
      const next = new URLSearchParams(searchParams.toString());
      mutate(next);
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
    },
    [pathname, router, searchParams],
  );

  const filterHref = React.useCallback(
    (state: StorageHealthSqlState) => {
      const next = new URLSearchParams(searchParams.toString());
      const f = storageHealthFilterParam({
        state,
        mediaType: parsed.mediaType,
      });
      if (f) next.set("f", f);
      else next.delete("f");
      next.delete("p");
      const qs = next.toString();
      return qs ? `${pathname}?${qs}` : pathname;
    },
    [parsed.mediaType, pathname, searchParams],
  );

  const openDetail = React.useCallback(
    (item: StorageHealthFile) => {
      replaceParams((next) => {
        next.set("mt", item.media_type);
        next.set("fid", item.file_id);
      });
    },
    [replaceParams],
  );

  const closeDetail = React.useCallback(() => {
    replaceParams((next) => {
      next.delete("mt");
      next.delete("fid");
    });
  }, [replaceParams]);

  const columns = React.useMemo<ColumnDef<StorageHealthFile>[]>(
    () => [
      {
        id: "title",
        header: "Title",
        width: "minmax(0,1fr)",
        mobile: { role: "title" },
        render: (row) => (
          <span className="truncate text-sm font-medium">
            {row.media_title || "Untitled"}
            {row.episode ? ` ${row.episode}` : ""}
          </span>
        ),
      },
      {
        id: "type",
        header: "Type",
        width: "72px",
        mobile: { role: "meta", order: 1 },
        render: (row) => <TypePill>{row.media_type === "show" ? "Show" : "Movie"}</TypePill>,
      },
      {
        id: "state",
        header: "State",
        width: "110px",
        mobile: { role: "meta", order: 0 },
        render: (row) => <StatusPill status={row.state} label={STATE_LABELS[row.state]} />,
      },
      {
        id: "quality",
        header: "Quality",
        width: "88px",
        hideBelow: "md",
        mobile: { role: "meta", order: 2 },
        render: (row) => <MetaPill>{qualityToString(row.quality)}</MetaPill>,
      },
    ],
    [],
  );

  const facets = React.useMemo<FacetDef<StorageHealthFile>[]>(
    () => [
      {
        id: "state",
        label: "State",
        options: [
          { value: "corrupt", label: "Corrupt" },
          { value: "unknown", label: "Not hashed" },
          { value: "orphaned", label: "Orphaned" },
          { value: "pending", label: "Pending" },
          { value: "healthy", label: "Healthy" },
        ],
        defaultOperator: "is",
        operators: ["is"],
        predicate: (_item, _values, _operator) => true,
      },
      {
        id: "media_type",
        label: "Type",
        options: [
          { value: "show", label: "Show" },
          { value: "movie", label: "Movie" },
        ],
        defaultOperator: "is",
        operators: ["is"],
        predicate: (_item, _values, _operator) => true,
      },
    ],
    [],
  );

  const countsView = storageHealthViewState({
    isPending: summaryQuery.isPending,
    isError: summaryQuery.isError,
    data: summaryQuery.data?.counts ?? null,
  });
  const pageItems = listQuery.data?.items ?? [];
  const items = searching ? (searchAllQuery.data ?? []) : pageItems;
  const unknownHint =
    summaryQuery.data != null
      ? storageHealthUnknownHint({
          integrityEnabled: summaryQuery.data.integrity_check_enabled,
          unknown: summaryQuery.data.counts.unknown,
        })
      : null;

  return (
    <>
      <div>
        <p className="text-sm text-muted-foreground">
          {summaryQuery.data?.freshness_note ??
            "Read-only library file condition. Integrity mismatch times are last import attempt, not audit time."}
        </p>
        {summaryQuery.data && (
          <p className="mt-1 text-xs text-muted-foreground">
            Integrity audit {summaryQuery.data.integrity_check_enabled ? "on" : "off"}
            {" · "}
            interval {summaryQuery.data.integrity_check_interval_hours}h
          </p>
        )}
      </div>

      {summaryQuery.isError && (
        <Alert variant="destructive">
          <AlertTitle>Failed to load storage</AlertTitle>
          <AlertDescription className="flex items-center gap-2">
            {STORAGE_HEALTH_ERROR_MESSAGE}
            <Button variant="outline" size="sm" onClick={() => summaryQuery.refetch()}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {countsView.status === "pending" && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="h-4 w-4 animate-spin" />
          Loading summary…
        </div>
      )}

      {countsView.status === "success" && (
        <>
          {(summaryQuery.data?.volumes.length ?? 0) > 0 && (
            <div className="grid gap-3 sm:grid-cols-2">
              {summaryQuery.data?.volumes.map((volume) => {
                const pct = volumeUsedPercent({
                  used_bytes: volume.used_bytes,
                  total_bytes: volume.total_bytes,
                });
                return (
                  <Card key={`${volume.label}-${volume.path}`} size="sm">
                    <CardHeader>
                      <CardTitle>{volume.label}</CardTitle>
                      <CardDescription className="font-mono text-xs break-all">
                        {volume.path || "—"}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-2">
                      {volume.error ? (
                        <p className="text-sm text-muted-foreground">{volume.error}</p>
                      ) : (
                        <>
                          <p className="text-sm tabular-nums">
                            {formatBytes(volume.free_bytes)} free
                            {" · "}
                            {formatBytes(volume.used_bytes)} of {formatBytes(volume.total_bytes)}
                          </p>
                          {pct != null && (
                            <Progress value={pct}>
                              <ProgressLabel className="text-xs">
                                {Math.round(pct)}% used
                              </ProgressLabel>
                            </Progress>
                          )}
                        </>
                      )}
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {summaryQuery.data?.libraries.map((lib) => (
              <Badge
                key={`${lib.kind}-${lib.name}-${lib.path}`}
                variant={lib.ok ? "outline" : "destructive"}
              >
                {lib.name} · {lib.kind}
                {lib.ok ? "" : ` · ${lib.error ?? "inaccessible"}`}
              </Badge>
            ))}
          </div>
          {(summaryQuery.data?.unconfigured_library_names.length ?? 0) > 0 && (
            <p className="text-xs text-muted-foreground">
              Unconfigured library names: {summaryQuery.data?.unconfigured_library_names.join(", ")}
              . Files were not marked missing.
            </p>
          )}
          {summaryQuery.data?.libraries.some((lib) => !lib.ok) && (
            <p className="text-xs text-muted-foreground">{STORAGE_HEALTH_COPY.inaccessible}</p>
          )}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Link href={filterHref("corrupt")} className="text-left">
              <StatCard title="Corrupt" footer="SHA1 mismatch stamps">
                {countsView.counts.corrupt}
              </StatCard>
            </Link>
            <Link href={filterHref("orphaned")} className="text-left">
              <StatCard title="Orphaned" footer="Ghost failed rows">
                {countsView.counts.orphaned}
              </StatCard>
            </Link>
            <div className="text-left">
              <Link href={filterHref("pending")} className="block">
                <StatCard title="Pending" footer="Acquisition recovery">
                  {countsView.counts.pending}
                </StatCard>
              </Link>
              <Link
                href={storageHealthImportsHref()}
                className="mt-1 inline-block text-xs text-muted-foreground underline"
              >
                Open Imports
              </Link>
            </div>
            <Link href={filterHref("unknown")} className="text-left">
              <StatCard
                title="Unknown"
                footer={
                  summaryQuery.data?.integrity_check_enabled
                    ? "Not yet hashed"
                    : STORAGE_HEALTH_COPY.hashingDisabled
                }
              >
                {countsView.counts.unknown}
              </StatCard>
            </Link>
            <div className="text-left">
              <StatCard title="Healthy" footer={`${countsView.counts.imported} imported`}>
                {countsView.counts.healthy}
              </StatCard>
            </div>
          </div>
          {unknownHint && <p className="text-xs text-muted-foreground">{unknownHint}</p>}
          <p className="text-xs text-muted-foreground">{STORAGE_HEALTH_COPY.missingNote}</p>
          {storageHealthAllClear(countsView.counts) && (
            <p className="text-sm text-muted-foreground">{STORAGE_HEALTH_COPY.allClear}</p>
          )}
        </>
      )}

      {listQuery.isError && (
        <Alert variant="destructive">
          <AlertTitle>Failed to load files</AlertTitle>
          <AlertDescription className="flex items-center gap-2">
            {STORAGE_HEALTH_ERROR_MESSAGE}
            <Button variant="outline" size="sm" onClick={() => listQuery.refetch()}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <DataList<StorageHealthFile>
        key={searchParams.get("f") ?? "all"}
        data={items}
        getId={(row) => `${row.media_type}:${row.file_id}`}
        columns={columns}
        facets={facets}
        disableSelection
        pageSize={50}
        pageSizeOptions={[20, 50, 100]}
        totalCount={searching ? undefined : listQuery.data?.total}
        onPaginationChange={searching ? undefined : onPaginationChange}
        searchPlaceholder="Search titles…"
        searchMatch={storageSearchMatch}
        onSearchChange={setSearchQuery}
        loading={searching ? searchAllQuery.isLoading : listQuery.isLoading && items.length === 0}
        density="compact"
        emptyIcon={<HardDrive />}
        emptyTitle="No matching files"
        emptyDescription={STORAGE_HEALTH_COPY.missingNote}
        onRowOpen={openDetail}
      />

      <Sheet
        open={Boolean(parsed.detailMediaType && parsed.detailFileId)}
        onOpenChange={(open) => {
          if (!open) closeDetail();
        }}
      >
        <SheetContent side="right" className="w-96 overflow-y-auto sm:max-w-md">
          <SheetHeader>
            <SheetTitle>
              {detailQuery.data?.media_title || "File"}
              {detailQuery.data?.episode ? ` ${detailQuery.data.episode}` : ""}
            </SheetTitle>
            <SheetDescription>
              Read-only file condition. Repair actions live on Imports.
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-3 px-4 pb-6">
            {detailQuery.isPending && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                Loading…
              </div>
            )}
            {detailQuery.isError && (
              <p className="text-sm text-muted-foreground">{STORAGE_HEALTH_ERROR_MESSAGE}</p>
            )}
            {detailQuery.data && (
              <>
                <StatusPill
                  status={detailQuery.data.state}
                  label={STATE_LABELS[detailQuery.data.state]}
                />
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-muted-foreground">Library</dt>
                  <dd>{detailQuery.data.library}</dd>
                  <dt className="text-muted-foreground">Quality</dt>
                  <dd>{qualityToString(detailQuery.data.quality)}</dd>
                  <dt className="text-muted-foreground">Variant</dt>
                  <dd>{detailQuery.data.variant_tag || "—"}</dd>
                  <dt className="text-muted-foreground">Import</dt>
                  <dd>{detailQuery.data.import_status}</dd>
                  <dt className="text-muted-foreground">Error</dt>
                  <dd className="break-all">{detailQuery.data.import_error || "—"}</dd>
                  <dt className="text-muted-foreground">SHA1</dt>
                  <dd className="font-mono text-xs break-all">{detailQuery.data.sha1 || "—"}</dd>
                  <dt className="text-muted-foreground">Path</dt>
                  <dd className="font-mono text-xs break-all">{detailQuery.data.path || "—"}</dd>
                </dl>
                <div className="flex flex-col gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    render={
                      <Link
                        href={storageHealthTitleHref(
                          detailQuery.data.media_type,
                          detailQuery.data.media_id,
                        )}
                      />
                    }
                  >
                    Open title
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    render={<Link href={storageHealthImportsHref()} />}
                  >
                    Open Imports
                  </Button>
                </div>
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
