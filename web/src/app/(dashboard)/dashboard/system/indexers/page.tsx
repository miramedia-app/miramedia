"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Network, Plus, TriangleAlert } from "lucide-react";

import { DashboardHeader } from "@/components/dashboard-header";
import { Button } from "@/components/ui/button";
import { DataList, DataListEmpty } from "@/components/data-list";
import apiClient from "@/lib/api/client";
import { useIndexerSites } from "@/hooks/use-indexer-sites";
import { useIndexerSiteTest } from "@/hooks/use-indexer-site-test";
import {
  INDEXER_FACETS,
  INDEXER_GROUPINGS,
  INDEXER_SORT_OPTIONS,
  IndexerRowActions,
  buildIndexerBulkActions,
  buildIndexerColumns,
  indexerSearchMatch,
} from "@/components/providers/indexer-list-config";
import { AddIndexerDialog } from "@/components/providers/add-indexer-dialog";
import { EditIndexerDialog } from "@/components/providers/edit-indexer-dialog";
import { ManageUrlsDialog } from "@/components/providers/manage-urls-dialog";
import type { Site } from "@/lib/indexers";

export default function IndexersPage() {
  const sitesApi = useIndexerSites();
  const {
    sitesQuery,
    sites,
    updateSite,
    deleteSite,
    invalidateSites,
    addOpen,
    setAddOpen,
    addLoading,
    newSite,
    setNewSite,
    addSite,
    editOpen,
    setEditOpen,
    editLoading,
    editSite,
    setEditSite,
    openEdit,
    saveEdit,
    urlOpen,
    setUrlOpen,
    urlLoading,
    urlSite,
    newUrl,
    setNewUrl,
    openUrls,
    switchActiveUrl,
    addUrl,
    removeUrl,
  } = sitesApi;

  const { testingId, testSite } = useIndexerSiteTest(invalidateSites);

  // Whether Cloudflare bypass is enabled in system settings. When disabled, the
  // per-indexer Cloudflare toggle is hidden. Defaults to shown until loaded.
  const settingsQuery = useQuery({
    queryKey: ["system", "settings"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/system/settings", { signal });
      if (error) throw error;
      return (data ?? {}) as { cloudflare?: { enabled?: boolean } };
    },
    staleTime: 5 * 60 * 1000,
  });
  const cfEnabled = settingsQuery.data?.cloudflare?.enabled ?? true;
  const loadError = sitesQuery.isError ? "Failed to load indexers" : null;

  const columns = React.useMemo(
    () => buildIndexerColumns({ updateSite, openUrls }),
    [updateSite, openUrls],
  );
  const bulkActions = React.useMemo(
    () => buildIndexerBulkActions(invalidateSites),
    [invalidateSites],
  );

  const renderRowActions = React.useCallback(
    (site: Site) => (
      <IndexerRowActions
        site={site}
        testingId={testingId}
        onTest={testSite}
        onEdit={openEdit}
        onManageUrls={openUrls}
        onDelete={(id, name) => void deleteSite(id, name)}
      />
    ),
    [testingId, testSite, openEdit, openUrls, deleteSite],
  );

  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "System", href: "/dashboard/system/users" },
          { label: "Indexers" },
        ]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        {loadError ? (
          <DataListEmpty
            icon={<TriangleAlert />}
            title={loadError}
            description="Check that the backend is reachable, then retry."
            action={
              <Button variant="outline" size="sm" onClick={() => sitesQuery.refetch()}>
                Retry
              </Button>
            }
          />
        ) : (
          <DataList<Site>
            data={sites}
            getId={(s) => s.id}
            searchPlaceholder="Search or filter indexers…"
            searchMatch={indexerSearchMatch}
            facets={INDEXER_FACETS}
            sortOptions={INDEXER_SORT_OPTIONS}
            defaultSort="name-asc"
            groupings={INDEXER_GROUPINGS}
            defaultGroupId="type"
            bulkActions={bulkActions}
            loading={sitesQuery.isLoading}
            density="rich"
            emptyIcon={<Network />}
            emptyTitle="No indexers configured"
            emptyDescription="Add a Torznab-compatible site to start searching."
            toolbarTrailing={
              <Button size="default" className="gap-1 text-xs" onClick={() => setAddOpen(true)}>
                <Plus className="h-4 w-4" />
                Add Indexer
              </Button>
            }
            columns={columns}
            rowActions={renderRowActions}
          />
        )}
      </main>

      <AddIndexerDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        newSite={newSite}
        setNewSite={setNewSite}
        loading={addLoading}
        onAdd={addSite}
        cfEnabled={cfEnabled}
      />

      <EditIndexerDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        editSite={editSite}
        setEditSite={setEditSite}
        loading={editLoading}
        onSave={saveEdit}
        cfEnabled={cfEnabled}
      />

      <ManageUrlsDialog
        open={urlOpen}
        onOpenChange={setUrlOpen}
        urlSite={urlSite}
        newUrl={newUrl}
        setNewUrl={setNewUrl}
        loading={urlLoading}
        onSwitchActive={switchActiveUrl}
        onAddUrl={addUrl}
        onRemoveUrl={removeUrl}
      />
    </>
  );
}
