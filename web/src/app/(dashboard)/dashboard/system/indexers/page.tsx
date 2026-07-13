"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  LoaderCircle,
  Plus,
  Trash2,
  FlaskConical,
  Shield,
  Pencil,
  Link as LinkIcon,
  X,
  EllipsisVertical,
  Network,
  Power,
  PowerOff,
} from "lucide-react";
import { DashboardHeader } from "@/components/dashboard-header";
import { StatusPill } from "@/components/ui/status-pill";
import { TypePill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DataList } from "@/components/data-list";
import type {
  BulkAction,
  ColumnDef,
  FacetDef,
  GroupByDef,
  SortOption,
} from "@/components/data-list";
import apiClient from "@/lib/api/client";
import { createManagedEventSource, type ManagedEventSource } from "@/lib/managed-event-source";

type Site = {
  id: string;
  name: string;
  url: string;
  available_urls?: string[];
  api_key?: string | null;
  supports_tv: boolean;
  supports_movies: boolean;
  cloudflare_protected?: boolean;
  site_type: string;
  enabled: boolean;
  is_preloaded?: boolean;
  priority?: number | null;
  last_test_status?: string | null;
  last_test_at?: string | null;
  last_success_at?: string | null;
};

const siteTypeLabel: Record<string, string> = {
  native: "System",
  torznab: "Custom",
};

export default function IndexersPage() {
  const qc = useQueryClient();

  const sitesQuery = useQuery({
    queryKey: ["indexers", "sites"],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/indexers/sites");
      return (data ?? []) as unknown as Site[];
    },
  });
  const sites = sitesQuery.data ?? [];

  const updateSite = React.useCallback(
    async (siteId: string, body: Record<string, unknown>) => {
      const { error } = await apiClient.PUT("/api/v1/indexers/sites/{site_id}", {
        params: { path: { site_id: siteId } },
        body: body as never,
      });
      if (!error) {
        await qc.invalidateQueries({ queryKey: ["indexers", "sites"] });
      } else {
        toast.error("Failed to update site");
      }
    },
    [qc],
  );

  const columns = React.useMemo<ColumnDef<Site>[]>(
    () => [
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
          <span className="text-sm text-muted-foreground tabular-nums">{s.priority ?? 100}</span>
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
    ],
    [updateSite],
  );

  const facets = React.useMemo<FacetDef<Site>[]>(
    () => [
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
          const v = s.last_test_status === "error" ? "error" : "ok";
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
    ],
    [],
  );

  const sortOptionsConfig = React.useMemo<SortOption<Site>[]>(
    () => [
      { id: "name-asc", label: "Name A–Z", compare: (a, b) => a.name.localeCompare(b.name) },
      { id: "name-desc", label: "Name Z–A", compare: (a, b) => b.name.localeCompare(a.name) },
      {
        id: "priority-asc",
        label: "Priority (low first)",
        compare: (a, b) => (a.priority ?? 100) - (b.priority ?? 100),
      },
      {
        id: "last-success-desc",
        label: "Last success (newest)",
        compare: (a, b) =>
          new Date(b.last_success_at ?? 0).getTime() - new Date(a.last_success_at ?? 0).getTime(),
      },
    ],
    [],
  );

  const groupings = React.useMemo<GroupByDef<Site>[]>(
    () => [
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
        getGroup: (s) => {
          if (s.last_test_status === "error")
            return { key: "failed", label: "Failed", sortOrder: 0 };
          if (s.last_success_at) return { key: "healthy", label: "Healthy", sortOrder: 1 };
          return { key: "untested", label: "Untested", sortOrder: 2 };
        },
      },
    ],
    [],
  );

  const bulkActions = React.useMemo<BulkAction<Site>[]>(
    () => [
      {
        id: "enable",
        label: "Enable",
        icon: <Power className="h-3.5 w-3.5" />,
        variant: "secondary",
        onRun: async (items) => {
          await Promise.all(items.map((s) => updateSite(s.id, { enabled: true })));
          toast.success(`Enabled ${items.length} site(s)`);
        },
      },
      {
        id: "disable",
        label: "Disable",
        icon: <PowerOff className="h-3.5 w-3.5" />,
        variant: "secondary",
        onRun: async (items) => {
          await Promise.all(items.map((s) => updateSite(s.id, { enabled: false })));
          toast.success(`Disabled ${items.length} site(s)`);
        },
      },
    ],
    [updateSite],
  );

  // Add dialog
  const [addOpen, setAddOpen] = React.useState(false);
  const [addLoading, setAddLoading] = React.useState(false);
  const [newSite, setNewSite] = React.useState({
    name: "",
    url: "",
    api_key: "",
    supports_tv: true,
    supports_movies: true,
    cloudflare_protected: false,
  });

  async function addSite() {
    setAddLoading(true);
    const { error } = await apiClient.POST("/api/v1/indexers/sites", {
      body: {
        name: newSite.name,
        url: newSite.url,
        available_urls: [newSite.url],
        api_key: newSite.api_key,
        supports_tv: newSite.supports_tv,
        supports_movies: newSite.supports_movies,
        cloudflare_protected: newSite.cloudflare_protected,
        site_type: "torznab",
        enabled: true,
        categories_tv: "5000",
        categories_movies: "2000",
        priority: 100,
      } as never,
    });
    setAddLoading(false);
    if (!error) {
      toast.success(`Added indexer site "${newSite.name}"`);
      setAddOpen(false);
      setNewSite({
        name: "",
        url: "",
        api_key: "",
        supports_tv: true,
        supports_movies: true,
        cloudflare_protected: false,
      });
      await qc.invalidateQueries({ queryKey: ["indexers", "sites"] });
    } else {
      toast.error("Failed to add site");
    }
  }

  async function deleteSite(siteId: string, siteName: string) {
    const { error } = await apiClient.DELETE("/api/v1/indexers/sites/{site_id}", {
      params: { path: { site_id: siteId } },
    });
    if (!error) {
      toast.success(`Deleted "${siteName}"`);
      await qc.invalidateQueries({ queryKey: ["indexers", "sites"] });
    } else {
      toast.error("Failed to delete site");
    }
  }

  // Edit dialog
  const [editOpen, setEditOpen] = React.useState(false);
  const [editLoading, setEditLoading] = React.useState(false);
  const [editSite, setEditSite] = React.useState<Site | null>(null);

  async function saveEdit() {
    if (!editSite) return;
    setEditLoading(true);
    const { error } = await apiClient.PUT("/api/v1/indexers/sites/{site_id}", {
      params: { path: { site_id: editSite.id } },
      body: {
        name: editSite.name,
        url: editSite.url,
        api_key: editSite.api_key,
        supports_tv: editSite.supports_tv,
        supports_movies: editSite.supports_movies,
        cloudflare_protected: editSite.cloudflare_protected,
        priority: Number.isFinite(editSite.priority) ? editSite.priority : 100,
      } as never,
    });
    setEditLoading(false);
    if (!error) {
      toast.success(`Updated "${editSite.name}"`);
      setEditOpen(false);
      setEditSite(null);
      await qc.invalidateQueries({ queryKey: ["indexers", "sites"] });
    } else {
      toast.error("Failed to update site");
    }
  }

  // URL management dialog
  const [urlOpen, setUrlOpen] = React.useState(false);
  const [urlLoading, setUrlLoading] = React.useState(false);
  const [urlSite, setUrlSite] = React.useState<Site | null>(null);
  const [newUrl, setNewUrl] = React.useState("");

  function openUrls(site: Site) {
    setUrlSite({ ...site, available_urls: [...(site.available_urls ?? [])] });
    setNewUrl("");
    setUrlOpen(true);
  }

  async function switchActiveUrl(url: string) {
    if (!urlSite) return;
    setUrlLoading(true);
    await updateSite(urlSite.id, { url });
    setUrlSite({ ...urlSite, url });
    setUrlLoading(false);
    toast.success("Active URL changed");
  }

  async function addUrl() {
    if (!urlSite || !newUrl.trim()) return;
    const trimmed = newUrl.trim();
    if ((urlSite.available_urls ?? []).includes(trimmed)) {
      toast.error("URL already exists");
      return;
    }
    setUrlLoading(true);
    const updatedUrls = [...(urlSite.available_urls ?? []), trimmed];
    await updateSite(urlSite.id, { available_urls: updatedUrls });
    setUrlSite({ ...urlSite, available_urls: updatedUrls });
    setNewUrl("");
    setUrlLoading(false);
  }

  async function removeUrl(url: string) {
    if (!urlSite) return;
    if (url === urlSite.url) {
      toast.error("Can't remove the active URL");
      return;
    }
    setUrlLoading(true);
    const updatedUrls = (urlSite.available_urls ?? []).filter((u) => u !== url);
    await updateSite(urlSite.id, { available_urls: updatedUrls });
    setUrlSite({ ...urlSite, available_urls: updatedUrls });
    setUrlLoading(false);
  }

  // Test
  const [testingId, setTestingId] = React.useState<string | null>(null);
  // Active test stream so re-testing / unmount aborts the prior one cleanly.
  const testStreamRef = React.useRef<ManagedEventSource | null>(null);
  React.useEffect(() => () => testStreamRef.current?.close(), []);

  // Stream a site test over SSE. The spinner stays on the row and a single
  // toast updates in place with each live phase ("Loading page…", "Solving
  // Turnstile (attempt 3)…"), then resolves to the real pass/fail — instead of
  // a blind multi-minute wait on one blocking request.
  function testSite(site: Site) {
    testStreamRef.current?.close();
    setTestingId(site.id);
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
    const url = new URL(
      `${apiBase}/api/v1/indexers/sites/${site.id}/test/stream`,
      window.location.origin,
    );
    const toastId = toast.loading(`Testing ${site.name}…`);

    let settled = false;
    const handle = createManagedEventSource(url.toString(), {
      withCredentials: true,
      // Cap on the stalled-error state, matching the prior 15s timer. The
      // readyState CLOSED (terminal) vs CONNECTING (transient) discrimination
      // now lives in the primitive.
      timeoutMs: 15000,
      doneEvent: "done",
      events: {
        status: (ev) => {
          try {
            const { message } = JSON.parse(ev.data) as { message?: string };
            if (message) toast.loading(message, { id: toastId });
          } catch (err) {
            console.error("SSE status parse error", err);
          }
        },
        result: (ev) => {
          settled = true;
          try {
            const r = JSON.parse(ev.data) as { success?: boolean; message?: string };
            if (r.success) toast.success(r.message ?? "OK", { id: toastId });
            else toast.error(r.message ?? "Test failed", { id: toastId });
          } catch {
            toast.error("Test failed", { id: toastId });
          }
        },
      },
      onDone: (outcome) => {
        // Same terminal toasts as before, keyed off the outcome the primitive
        // reports: CLOSED → "connection lost", timer cap → "timed out". A
        // normal "done" event ("completed") leaves the result toast untouched.
        if (!settled) {
          if (outcome === "closed") {
            toast.error("Test failed — connection lost", { id: toastId });
          } else if (outcome === "timeout") {
            toast.error("Test timed out", { id: toastId });
          }
        }
        if (testStreamRef.current === handle) testStreamRef.current = null;
        setTestingId((cur) => (cur === site.id ? null : cur));
        void qc.invalidateQueries({ queryKey: ["indexers", "sites"] });
      },
    });
    testStreamRef.current = handle;
  }

  const renderRowActions = React.useCallback(
    (site: Site) => (
      <>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground"
          disabled={testingId === site.id}
          onClick={(e) => {
            e.stopPropagation();
            void testSite(site);
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
            setEditSite({ ...site });
            setEditOpen(true);
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
            <DropdownMenuItem onClick={() => openUrls(site)}>
              <LinkIcon className="mr-2 h-4 w-4" />
              Manage URLs
            </DropdownMenuItem>
            {!site.is_preloaded && (
              <DropdownMenuItem
                className="text-destructive"
                onClick={() => void deleteSite(site.id, site.name)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </>
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [testingId],
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
          searchMatch={(s, q) =>
            s.name.toLowerCase().includes(q) || (s.url ?? "").toLowerCase().includes(q)
          }
          facets={facets}
          sortOptions={sortOptionsConfig}
          defaultSort="name-asc"
          groupings={groupings}
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

      {/* Add Dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Add Indexer Site</DialogTitle>
            <DialogDescription>Add a custom Torznab-compatible indexer site.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label>Name</Label>
              <Input
                value={newSite.name}
                onChange={(e) => setNewSite((s) => ({ ...s, name: e.target.value }))}
                placeholder="My Private Tracker"
              />
            </div>
            <div className="grid gap-2">
              <Label>Torznab URL</Label>
              <Input
                value={newSite.url}
                onChange={(e) => setNewSite((s) => ({ ...s, url: e.target.value }))}
                placeholder="https://tracker.example.com/torznab"
              />
            </div>
            <div className="grid gap-2">
              <Label>API Key</Label>
              <Input
                value={newSite.api_key}
                onChange={(e) => setNewSite((s) => ({ ...s, api_key: e.target.value }))}
                placeholder="Optional"
                type="password"
              />
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Switch
                  checked={newSite.supports_tv}
                  onCheckedChange={(v) => setNewSite((s) => ({ ...s, supports_tv: v }))}
                />
                <Label>Shows</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={newSite.supports_movies}
                  onCheckedChange={(v) => setNewSite((s) => ({ ...s, supports_movies: v }))}
                />
                <Label>Movies</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={newSite.cloudflare_protected}
                  onCheckedChange={(v) => setNewSite((s) => ({ ...s, cloudflare_protected: v }))}
                />
                <Label>CF Protected</Label>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              onClick={() => void addSite()}
              disabled={addLoading || !newSite.name || !newSite.url}
            >
              {addLoading ? (
                <>
                  <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                  Adding...
                </>
              ) : (
                "Add Site"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Edit Indexer Site</DialogTitle>
            <DialogDescription>Modify settings for this indexer site.</DialogDescription>
          </DialogHeader>
          {editSite && (
            <div className="grid gap-4 py-4">
              <div className="grid gap-2">
                <Label>Name</Label>
                <Input
                  value={editSite.name}
                  onChange={(e) => setEditSite((s) => (s ? { ...s, name: e.target.value } : s))}
                />
              </div>
              <div className="grid gap-2">
                <Label>Active URL</Label>
                {(editSite.available_urls ?? []).length > 1 ? (
                  <Select
                    value={editSite.url}
                    onValueChange={(v) => setEditSite((s) => (s ? { ...s, url: v } : s))}
                  >
                    <SelectTrigger>
                      <span className="truncate">{editSite.url}</span>
                    </SelectTrigger>
                    <SelectContent>
                      {(editSite.available_urls ?? []).map((url) => (
                        <SelectItem key={url} value={url}>
                          {url}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    value={editSite.url}
                    onChange={(e) => setEditSite((s) => (s ? { ...s, url: e.target.value } : s))}
                  />
                )}
              </div>
              <div className="grid gap-2">
                <Label>Priority</Label>
                <Input
                  type="number"
                  min={0}
                  value={editSite.priority ?? 100}
                  onChange={(e) =>
                    setEditSite((s) => (s ? { ...s, priority: parseInt(e.target.value, 10) } : s))
                  }
                />
                <span className="text-xs text-muted-foreground">Lower = searched first.</span>
              </div>
              {editSite.site_type === "torznab" && (
                <div className="grid gap-2">
                  <Label>API Key</Label>
                  <Input
                    value={editSite.api_key ?? ""}
                    onChange={(e) =>
                      setEditSite((s) => (s ? { ...s, api_key: e.target.value } : s))
                    }
                    type="password"
                  />
                </div>
              )}
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={editSite.supports_tv}
                    onCheckedChange={(v) => setEditSite((s) => (s ? { ...s, supports_tv: v } : s))}
                  />
                  <Label>Shows</Label>
                </div>
                <div className="flex items-center gap-2">
                  <Switch
                    checked={editSite.supports_movies}
                    onCheckedChange={(v) =>
                      setEditSite((s) => (s ? { ...s, supports_movies: v } : s))
                    }
                  />
                  <Label>Movies</Label>
                </div>
                <div className="flex items-center gap-2">
                  <Switch
                    checked={editSite.cloudflare_protected}
                    onCheckedChange={(v) =>
                      setEditSite((s) => (s ? { ...s, cloudflare_protected: v } : s))
                    }
                  />
                  <Label>CF Protected</Label>
                </div>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button
              onClick={() => void saveEdit()}
              disabled={editLoading || !editSite?.name || !editSite?.url}
            >
              {editLoading ? (
                <>
                  <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                  Saving...
                </>
              ) : (
                "Save"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* URL Management */}
      <Dialog open={urlOpen} onOpenChange={setUrlOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>Manage URLs — {urlSite?.name}</DialogTitle>
            <DialogDescription>
              Select the active URL or add custom mirrors. The active URL is used for searches.
            </DialogDescription>
          </DialogHeader>
          {urlSite && (
            <div className="grid gap-3 py-4">
              {(urlSite.available_urls ?? []).map((url) => (
                <div key={url} className="flex items-center gap-2 rounded-md border p-2">
                  <button
                    type="button"
                    className={`flex-1 truncate text-left text-sm ${
                      url === urlSite.url
                        ? "font-semibold text-foreground"
                        : "text-muted-foreground"
                    }`}
                    onClick={() => void switchActiveUrl(url)}
                    disabled={urlLoading}
                  >
                    {url}
                  </button>
                  {url === urlSite.url ? (
                    <StatusPill status="active" label="Active" className="shrink-0" />
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 w-6 shrink-0 p-0"
                      onClick={() => void removeUrl(url)}
                      disabled={urlLoading}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              ))}
              <div className="flex gap-2">
                <Input
                  value={newUrl}
                  onChange={(e) => setNewUrl(e.target.value)}
                  placeholder="https://mirror.example.com"
                  className="flex-1"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void addUrl();
                  }}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void addUrl()}
                  disabled={urlLoading || !newUrl.trim()}
                >
                  <Plus className="mr-1 h-3 w-3" />
                  Add
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
