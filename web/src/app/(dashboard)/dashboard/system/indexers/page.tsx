"use client";

import * as React from "react";
import { Network, Plus } from "lucide-react";

import { DashboardHeader } from "@/components/dashboard-header";
import { Button } from "@/components/ui/button";
import { DataList } from "@/components/data-list";
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
            <Button size="default" className="text-xs" onClick={() => setAddOpen(true)}>
              <Plus className="mr-1 h-4 w-4" />
              Add site
            </Button>
          }
          columns={columns}
          rowActions={renderRowActions}
        />
      </main>

      <AddIndexerDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        newSite={newSite}
        setNewSite={setNewSite}
        loading={addLoading}
        onAdd={addSite}
      />

      <EditIndexerDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        editSite={editSite}
        setEditSite={setEditSite}
        loading={editLoading}
        onSave={saveEdit}
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
