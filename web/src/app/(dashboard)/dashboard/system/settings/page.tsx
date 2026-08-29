"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Download,
  LoaderCircle,
  Check,
  RotateCcw,
  Search as SearchIcon,
  TriangleAlert,
  Upload,
  X as XIcon,
} from "lucide-react";
import { DashboardHeader } from "@/components/dashboard-header";
import { DataListEmpty } from "@/components/data-list/data-list-empty";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import apiClient from "@/lib/api/client";
import { OverrideCtx, type AnyObj, type OverrideCtxValue } from "./_shared";
import { retrySettingsReads, settingsReadViewState } from "./settings-read-state";
import { INDEXER_SCORING_KEYS, useSettingsEditor } from "./use-settings-editor";

// Mimics the settings page layout (toolbar + sidebar tab list + form area)
// so the load state doesn't shift the page when content arrives.
function SettingsPageSkeleton() {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Skeleton className="h-8 min-w-[260px] flex-1" />
        <Skeleton className="h-8 w-24" />
        <Skeleton className="h-8 w-24" />
        <Skeleton className="h-8 w-32" />
      </div>
      <div className="grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          {Array.from({ length: 12 }, (_, i) => (
            <Skeleton key={`tab-${i}`} className="h-9 w-full" />
          ))}
        </div>
        <SettingsTabSkeleton />
      </div>
    </>
  );
}

// Used both for the in-tab Suspense fallback (lazy chunk load) and as the
// right pane of the full-page skeleton. Approximates two cards of form
// fields without committing to a specific tab's exact shape.
function SettingsTabSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {[0, 1].map((card) => (
        <div key={card} className="rounded-lg border bg-card p-6">
          <Skeleton className="mb-2 h-5 w-40" />
          <Skeleton className="mb-6 h-4 w-72" />
          <div className="grid gap-4 md:grid-cols-2">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={`f-${card}-${i}`} className="flex flex-col gap-1.5">
                <Skeleton className="h-3.5 w-24" />
                <Skeleton className="h-9 w-full" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

type Settings = AnyObj & {
  overrides?: AnyObj;
  defaults?: AnyObj;
  misc?: AnyObj;
  auth?: AnyObj;
  notifications?: AnyObj;
  torrents?: AnyObj;
  indexers?: AnyObj;
  metadata?: AnyObj;
  requests?: AnyObj;
  subtitles?: AnyObj;
  updates?: AnyObj;
  imports?: AnyObj;
  cloudflare?: AnyObj;
  watchlists?: AnyObj;
  streams?: AnyObj;
  playback?: AnyObj;
};
type SchemaEntry = {
  path: string[];
  section: string;
  key: string;
  label: string;
  description: string;
  type: string;
};

const TAB_DEFS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "general", label: "General" },
  { value: "updates", label: "Updates" },
  { value: "metadata", label: "Metadata" },
  { value: "torrents", label: "Torrents" },
  { value: "indexers", label: "Indexers" },
  { value: "scores", label: "Scores" },
  { value: "subtitles", label: "Subtitles" },
  { value: "imports", label: "Imports" },
  { value: "playback", label: "Playback" },
  { value: "watchlists", label: "Watchlists" },
  { value: "requests", label: "Requests" },
  { value: "notifications", label: "Notifications" },
  { value: "auth", label: "Authentication" },
];

// Tabs are code-split via React.lazy so opening Settings only loads the
// initially-active tab. Suspense boundary in the render handles fallback.
const AuthTab = React.lazy(() => import("./tabs/auth-tab").then((m) => ({ default: m.AuthTab })));
const RequestsTab = React.lazy(() =>
  import("./tabs/requests-tab").then((m) => ({ default: m.RequestsTab })),
);
const WatchlistsTab = React.lazy(() =>
  import("./tabs/watchlists-tab").then((m) => ({ default: m.WatchlistsTab })),
);
const PlaybackTab = React.lazy(() =>
  import("./tabs/playback-tab").then((m) => ({ default: m.PlaybackTab })),
);
const IndexersTab = React.lazy(() =>
  import("./tabs/indexers-tab").then((m) => ({ default: m.IndexersTab })),
);
const ImportsTab = React.lazy(() =>
  import("./tabs/imports-tab").then((m) => ({ default: m.ImportsTab })),
);
const MetadataTab = React.lazy(() =>
  import("./tabs/metadata-tab").then((m) => ({ default: m.MetadataTab })),
);
const UpdatesTab = React.lazy(() =>
  import("./tabs/updates-tab").then((m) => ({ default: m.UpdatesTab })),
);
const SubtitlesTab = React.lazy(() =>
  import("./tabs/subtitles-tab").then((m) => ({ default: m.SubtitlesTab })),
);
const NotificationsTab = React.lazy(() =>
  import("./tabs/notifications-tab").then((m) => ({ default: m.NotificationsTab })),
);
const TorrentsTab = React.lazy(() =>
  import("./tabs/torrents-tab").then((m) => ({ default: m.TorrentsTab })),
);
const ScoresTab = React.lazy(() =>
  import("./tabs/scores-tab").then((m) => ({ default: m.ScoresTab })),
);
const GeneralTab = React.lazy(() =>
  import("./tabs/general-tab").then((m) => ({ default: m.GeneralTab })),
);

export default function SystemSettingsPage() {
  const settingsQuery = useQuery({
    queryKey: ["system", "settings"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/system/settings", { signal });
      if (error) throw error;
      return (data ?? {}) as Settings;
    },
  });
  const schemaQuery = useQuery({
    queryKey: ["system", "settings", "schema"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/system/settings/schema", { signal });
      if (error) throw error;
      return (data ?? []) as unknown as SchemaEntry[];
    },
  });
  const readView = settingsReadViewState({
    settingsIsPending: settingsQuery.isPending,
    settingsIsError: settingsQuery.isError,
    schemaIsPending: schemaQuery.isPending,
    schemaIsError: schemaQuery.isError,
  });
  const settings = React.useMemo(
    () => settingsQuery.data ?? ({} as Settings),
    [settingsQuery.data],
  );
  const schema = React.useMemo(() => schemaQuery.data ?? [], [schemaQuery.data]);
  // Ready only after both reads succeed — do not gate on misc presence (empty
  // optional sections are valid) or coerce failed reads into an editable form.
  const loaded = readView === "ready";

  const editor = useSettingsEditor({ settings, loaded });

  // URL is source of truth for the active tab — no useState/effect mirror.
  // Clicks call setActiveTab which writes to the URL; readers derive from it.
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tabParam = searchParams?.get("tab") ?? null;
  const activeTab = tabParam && TAB_DEFS.some((t) => t.value === tabParam) ? tabParam : "general";
  const setActiveTab = React.useCallback(
    (tab: string) => {
      const params = new URLSearchParams(searchParams?.toString() ?? "");
      if (tab === "general") params.delete("tab");
      else params.set("tab", tab);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );
  const [searchQuery, setSearchQuery] = React.useState("");

  const searchResults = React.useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (q.length < 2) return [] as SchemaEntry[];
    return schema
      .filter(
        (entry) =>
          entry.label.toLowerCase().includes(q) ||
          entry.key.toLowerCase().includes(q) ||
          entry.description.toLowerCase().includes(q),
      )
      .slice(0, 10);
  }, [schema, searchQuery]);

  function jumpToEntry(entry: SchemaEntry) {
    const [section, ...rest] = entry.path;
    let tab = section ?? "general";
    if (section === "misc") tab = "general";
    if (section === "streams") tab = "playback";
    if (section === "indexers" && INDEXER_SCORING_KEYS.has(rest[0] ?? "")) tab = "scores";
    setActiveTab(tab);
    setSearchQuery("");
    toast.info(`In ${entry.section}: ${entry.label}`, { description: entry.key });
  }

  const overrideCtx = React.useMemo<OverrideCtxValue>(
    () => ({
      isOverridden: editor.isOverridden,
      defaults: editor.defaults,
      resetField: editor.resetField,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [editor.overrides, editor.defaults],
  );

  return (
    <OverrideCtx.Provider value={overrideCtx}>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "System", href: "/dashboard/system/users" },
          { label: "Settings" },
        ]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        {readView === "pending" ? (
          <SettingsPageSkeleton />
        ) : readView === "error" ? (
          <DataListEmpty
            icon={<TriangleAlert />}
            title="Failed to load settings"
            description="Check that the server is reachable and try again."
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  retrySettingsReads({
                    settingsIsError: settingsQuery.isError,
                    schemaIsError: schemaQuery.isError,
                    refetchSettings: () => settingsQuery.refetch(),
                    refetchSchema: () => schemaQuery.refetch(),
                  })
                }
              >
                Retry
              </Button>
            }
          />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative flex h-8 min-w-[260px] flex-1 items-center gap-1.5 rounded-md border border-input bg-background px-2.5 text-sm shadow-xs transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50">
                <SearchIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <input
                  type="search"
                  placeholder="Search settings…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="min-w-[80px] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                />
                {searchQuery && (
                  <button
                    type="button"
                    className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={() => setSearchQuery("")}
                    aria-label="Clear"
                  >
                    <XIcon className="h-3.5 w-3.5" />
                  </button>
                )}
                {searchResults.length > 0 ? (
                  <div className="absolute top-full right-0 left-0 z-10 mt-1 max-h-72 overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-md">
                    {searchResults.map((entry) => (
                      <button
                        key={entry.key + entry.path.join(".")}
                        type="button"
                        className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-accent hover:text-accent-foreground"
                        onClick={() => jumpToEntry(entry)}
                      >
                        <span className="text-sm font-medium">{entry.label}</span>
                        <span className="text-xs text-muted-foreground">
                          {entry.key} · {entry.type}
                        </span>
                        {entry.description && (
                          <span className="text-xs text-muted-foreground">{entry.description}</span>
                        )}
                      </button>
                    ))}
                  </div>
                ) : searchQuery.trim().length >= 2 ? (
                  <div className="absolute top-full right-0 left-0 z-10 mt-1 rounded-md border bg-popover px-3 py-2 text-sm text-muted-foreground shadow-md">
                    No matching settings.
                  </div>
                ) : null}
              </div>
              <span className="hidden h-6 w-px bg-border sm:block" />
              <Button
                variant="outline"
                size="default"
                className="text-xs"
                onClick={() => void editor.exportSettings()}
                disabled={editor.exporting}
              >
                {editor.exporting ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Download className="mr-1 h-4 w-4" />
                )}
                Export
              </Button>
              <Button
                variant="outline"
                size="default"
                className="text-xs"
                onClick={editor.pickImportFile}
                disabled={editor.importing}
              >
                {editor.importing ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="mr-1 h-4 w-4" />
                )}
                Import
              </Button>
              <Button
                variant="outline"
                size="default"
                className="text-xs"
                onClick={() => void editor.resetAll()}
                disabled={editor.resetting}
              >
                {editor.resetting ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="mr-1 h-4 w-4" />
                )}
                Reset All
              </Button>
              <span className="hidden h-6 w-px bg-border sm:block" />
              <div className="fixed inset-x-0 bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-30 flex items-center justify-end gap-3 border-t bg-background/95 px-4 py-2 backdrop-blur md:static md:bottom-auto md:z-auto md:border-0 md:bg-transparent md:p-0 md:backdrop-blur-none">
                {editor.isDirty && (
                  <span className="text-xs text-muted-foreground">
                    {editor.dirtyTabs.size} unsaved{" "}
                    {editor.dirtyTabs.size === 1 ? "section" : "sections"}
                  </span>
                )}
                <Button
                  onClick={() => void editor.saveAllSettings()}
                  disabled={editor.saving || !editor.isDirty}
                  size="default"
                  className="gap-1 text-xs max-md:min-w-28"
                >
                  {editor.saving ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <Check className="h-4 w-4" />
                  )}
                  Save
                </Button>
              </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full pb-16 md:pb-0">
              <div className="grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
                <TabsList className="sticky top-4 flex h-auto flex-col items-stretch justify-start self-start bg-transparent p-0 max-md:static max-md:-mx-4 max-md:snap-x max-md:snap-mandatory max-md:[scrollbar-width:none] max-md:flex-row max-md:gap-1 max-md:overflow-x-auto max-md:px-4 max-md:py-1 max-md:[&::-webkit-scrollbar]:hidden">
                  {TAB_DEFS.map(({ value, label }) => (
                    <TabsTrigger
                      key={value}
                      value={value}
                      className="relative justify-start rounded-md px-3 py-2 text-left text-sm font-medium data-[active]:border-border data-[active]:bg-muted data-[active]:shadow-none! max-md:min-h-11 max-md:shrink-0 max-md:snap-start"
                    >
                      {label}
                      {editor.dirtyTabs.has(value) && (
                        <span
                          className="ml-auto h-2 w-2 rounded-full bg-primary"
                          title="Unsaved changes"
                          aria-label={`Unsaved changes in ${label}`}
                        />
                      )}
                    </TabsTrigger>
                  ))}
                </TabsList>
                <div className="min-w-0">
                  <React.Suspense fallback={<SettingsTabSkeleton />}>
                    <TabsContent value="general">
                      <GeneralTab
                        misc={editor.misc}
                        setMiscPath={editor.setMiscPath}
                        cloudflare={editor.cloudflare}
                        setCloudflarePath={editor.setCloudflarePath}
                      />
                    </TabsContent>

                    <TabsContent value="torrents">
                      <TorrentsTab
                        misc={editor.misc}
                        setMiscPath={editor.setMiscPath}
                        torrents={editor.torrents}
                        setTorrentsPath={editor.setTorrentsPath}
                      />
                    </TabsContent>

                    <TabsContent value="indexers">
                      <IndexersTab
                        indexers={editor.indexers}
                        setIndexersPath={editor.setIndexersPath}
                        misc={editor.misc}
                        setMiscPath={editor.setMiscPath}
                      />
                    </TabsContent>

                    <TabsContent value="scores">
                      <ScoresTab
                        indexers={editor.indexers}
                        setIndexersPath={editor.setIndexersPath}
                      />
                    </TabsContent>

                    <TabsContent value="notifications">
                      <NotificationsTab
                        notifications={editor.notifications}
                        setNotificationsPath={editor.setNotificationsPath}
                      />
                    </TabsContent>

                    <TabsContent value="metadata">
                      <MetadataTab
                        metadata={editor.metadata}
                        setMetadataPath={editor.setMetadataPath}
                      />
                    </TabsContent>

                    <TabsContent value="watchlists">
                      <WatchlistsTab
                        watchlists={editor.watchlists}
                        setWatchlistsPath={editor.setWatchlistsPath}
                      />
                    </TabsContent>

                    <TabsContent value="playback">
                      <PlaybackTab
                        streams={editor.streams}
                        setStreamsPath={editor.setStreamsPath}
                        playback={editor.playback}
                        setPlaybackPath={editor.setPlaybackPath}
                      />
                    </TabsContent>

                    <TabsContent value="requests">
                      <RequestsTab
                        requests={editor.requests}
                        setRequestsPath={editor.setRequestsPath}
                      />
                    </TabsContent>

                    <TabsContent value="imports">
                      <ImportsTab
                        imports={editor.imports}
                        setImportsPath={editor.setImportsPath}
                        misc={editor.misc}
                        setMiscPath={editor.setMiscPath}
                      />
                    </TabsContent>

                    <TabsContent value="subtitles">
                      <SubtitlesTab
                        subtitles={editor.subtitles}
                        setSubtitlesPath={editor.setSubtitlesPath}
                      />
                    </TabsContent>

                    <TabsContent value="updates" className="space-y-4">
                      <UpdatesTab updates={editor.updates} setUpdatesPath={editor.setUpdatesPath} />
                    </TabsContent>

                    <TabsContent value="auth" className="space-y-4">
                      <AuthTab auth={editor.auth} setAuthPath={editor.setAuthPath} />
                    </TabsContent>
                  </React.Suspense>
                  <p className="mt-4 text-xs text-muted-foreground">
                    Saved values override{" "}
                    <code className="rounded bg-muted px-1 py-0.5 text-[0.7rem]">config.toml</code>.
                    Fields with an{" "}
                    <Badge variant="outline" className="mx-0.5 text-[0.7rem]">
                      overridden
                    </Badge>{" "}
                    badge differ from the file default — hover for the original or click the reset
                    icon to revert. Changes apply immediately, no restart required.
                  </p>
                </div>
              </div>
            </Tabs>
          </>
        )}
      </main>
    </OverrideCtx.Provider>
  );
}
