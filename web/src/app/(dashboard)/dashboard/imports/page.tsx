"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import {
  FolderInput,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  ScanLine,
  TriangleAlert,
} from "lucide-react";

import { DashboardHeader } from "@/components/dashboard-header";
import { Button } from "@/components/ui/button";
import { ManualMapDialog } from "@/components/imports/manual-map-dialog";
import { ChooseDestinationDialog } from "@/components/imports/choose-destination-dialog";
import { ImportRowActions } from "@/components/imports/import-row-actions";
import {
  IMPORT_FACETS,
  IMPORT_GROUPINGS,
  ImportExpandedContent,
  buildImportColumns,
  importSearchMatch,
  isImportExpandable,
} from "@/components/imports/import-list-config";
import { DataList, DataListEmpty } from "@/components/data-list";
import type { BulkAction } from "@/components/data-list";
import { useImportsQueue } from "@/hooks/use-imports-queue";
import type { ImportItem, ScanImport } from "@/lib/imports";

export default function ImportsPage() {
  const searchParams = useSearchParams();
  // Mirror DataList's URL-synced page/pageSize so the query key tracks the
  // server page immediately (including deep links with ?p=).
  const [listPage, setListPage] = React.useState(() => {
    const pageRaw = searchParams.get("p");
    const psRaw = searchParams.get("ps");
    return {
      page: pageRaw ? Math.max(1, Number.parseInt(pageRaw, 10) || 1) : 1,
      pageSize: psRaw ? Math.max(1, Number.parseInt(psRaw, 10) || 50) : 50,
    };
  });
  const onPaginationChange = React.useCallback((next: { page: number; pageSize: number }) => {
    setListPage((prev) =>
      prev.page === next.page && prev.pageSize === next.pageSize ? prev : next,
    );
  }, []);
  // Tab filter (?f=) is outside DataList — reset to page 1 when it changes so
  // the user doesn't land on an out-of-range page for the new tab.
  const filterParam = searchParams.get("f");
  React.useEffect(() => {
    setListPage((prev) => (prev.page === 1 ? prev : { ...prev, page: 1 }));
  }, [filterParam]);
  const {
    items,
    totalCount,
    isLoading,
    listView,
    refetchList,
    scanState,
    busyId,
    stagedByScan,
    setStagedByScan,
    queuedScanIds,
    effectiveChoiceFor,
    refreshAll,
    triggerScan,
    resolveTorrentRetry,
    pickScanCandidate,
    pickProviderCandidate,
    resolveIntegrity,
    ignoreItem,
    bulkRetry,
    bulkImport,
  } = useImportsQueue(filterParam, listPage.page, listPage.pageSize);

  const [mapDialogTorrent, setMapDialogTorrent] = React.useState<{
    id: string;
    title: string;
  } | null>(null);
  const [candidateModalScan, setCandidateModalScan] = React.useState<ScanImport | null>(null);

  const clearStaged = React.useCallback(
    (scanId: string) => {
      setStagedByScan((prev) => {
        const next = { ...prev };
        delete next[scanId];
        return next;
      });
    },
    [setStagedByScan],
  );

  const columns = React.useMemo(
    () =>
      buildImportColumns({
        stagedByScan,
        onChooseDestination: (it) => setCandidateModalScan(it as ScanImport),
      }),
    [stagedByScan],
  );

  const bulkActions = React.useMemo<BulkAction<ImportItem>[]>(
    () => [
      {
        id: "import",
        label: "Import",
        icon: <FolderInput className="h-3.5 w-3.5" />,
        onRun: (items) => void bulkImport(items),
      },
      {
        id: "retry",
        label: "Retry",
        icon: <RotateCcw className="h-3.5 w-3.5" />,
        variant: "secondary",
        onRun: (items) => void bulkRetry(items),
      },
    ],
    [bulkImport, bulkRetry],
  );

  const renderRowActions = React.useCallback(
    (it: ImportItem) => (
      <ImportRowActions
        item={it}
        busyId={busyId}
        queuedScanIds={queuedScanIds}
        effectiveChoiceFor={effectiveChoiceFor}
        onResolveIntegrity={(item, action) => void resolveIntegrity(item, action)}
        onMapTorrent={setMapDialogTorrent}
        onRetryTorrent={(item) => void resolveTorrentRetry(item)}
        onIgnore={(item) => void ignoreItem(item)}
        onPickScanCandidate={(item, c) => void pickScanCandidate(item, c)}
        onPickProviderCandidate={(item, c) => void pickProviderCandidate(item, c)}
        onClearStaged={clearStaged}
      />
    ),
    [
      busyId,
      queuedScanIds,
      effectiveChoiceFor,
      resolveIntegrity,
      resolveTorrentRetry,
      ignoreItem,
      pickScanCandidate,
      pickProviderCandidate,
      clearStaged,
    ],
  );

  return (
    <>
      <DashboardHeader
        crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Imports" }]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        {listView === "error" ? (
          <DataListEmpty
            icon={<TriangleAlert />}
            title="Imports could not be loaded"
            description="The import list request failed. Check that the backend is reachable."
            action={
              <Button variant="outline" size="sm" onClick={() => void refetchList()}>
                Retry
              </Button>
            }
          />
        ) : (
          <DataList<ImportItem>
            data={items}
            getId={(it) => it.id}
            columns={columns}
            pageSize={50}
            totalCount={totalCount}
            onPaginationChange={onPaginationChange}
            searchPlaceholder="Search or filter imports…"
            searchMatch={importSearchMatch}
            loading={isLoading && items.length === 0}
            density="rich"
            groupings={IMPORT_GROUPINGS}
            defaultGroupId="bucket"
            collapseStorageKey="imports"
            facets={IMPORT_FACETS}
            emptyIcon={<FolderInput />}
            emptyTitle="No imports yet"
            emptyDescription="Run a scan to surface library candidates."
            toolbarTrailing={
              <>
                <Button
                  size="default"
                  variant="outline"
                  className="text-xs"
                  onClick={() => void triggerScan()}
                  disabled={scanState === "running"}
                >
                  {scanState === "running" ? (
                    <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                  ) : (
                    <ScanLine className="mr-1 h-4 w-4" />
                  )}
                  Scan
                </Button>
                <Button
                  size="default"
                  variant="outline"
                  className="text-xs"
                  onClick={refreshAll}
                  disabled={isLoading}
                >
                  {isLoading ? (
                    <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-1 h-4 w-4" />
                  )}
                  Refresh
                </Button>
              </>
            }
            bulkActions={bulkActions}
            isExpandable={isImportExpandable}
            expandedContent={(it) => <ImportExpandedContent item={it} />}
            rowActions={renderRowActions}
          />
        )}
      </main>

      {mapDialogTorrent && (
        <ManualMapDialog
          torrentId={mapDialogTorrent.id}
          torrentTitle={mapDialogTorrent.title}
          open={mapDialogTorrent !== null}
          onOpenChange={(open) => {
            if (!open) setMapDialogTorrent(null);
          }}
          onApplied={() => {
            setMapDialogTorrent(null);
            refreshAll();
          }}
        />
      )}

      <ChooseDestinationDialog
        scan={candidateModalScan}
        busyId={busyId}
        stagedByScan={stagedByScan}
        onStage={(scanId, choice) => {
          setStagedByScan((prev) => ({ ...prev, [scanId]: choice }));
          setCandidateModalScan(null);
        }}
        onOpenChange={(open) => {
          if (!open) setCandidateModalScan(null);
        }}
      />
    </>
  );
}
