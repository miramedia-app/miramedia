"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import apiClient from "@/lib/api/client";
import { type MirrorEntry, type Site, siteMirrors } from "@/lib/indexers";

const SITES_KEY = ["indexers", "sites"];

export interface NewSiteForm {
  name: string;
  url: string;
  api_key: string;
  supports_tv: boolean;
  supports_movies: boolean;
  cloudflare_protected: boolean;
  enabled: boolean;
}

const EMPTY_NEW_SITE: NewSiteForm = {
  name: "",
  url: "",
  api_key: "",
  supports_tv: true,
  supports_movies: true,
  cloudflare_protected: false,
  enabled: true,
};

/**
 * Indexer-sites data layer: the sites query, the shared update mutation, and
 * the add / edit / URL-management dialog state plus their submit handlers.
 * Query key, request payloads, invalidation, and toast copy are preserved
 * exactly from the original page.
 */
export function useIndexerSites() {
  const qc = useQueryClient();

  const sitesQuery = useQuery({
    queryKey: SITES_KEY,
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/indexers/sites", { signal });
      if (error) throw error;
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
        await qc.invalidateQueries({ queryKey: SITES_KEY });
      } else {
        toast.error("Failed to update site");
      }
    },
    [qc],
  );

  // ── Add dialog ──────────────────────────────────────────────────────────
  const [addOpen, setAddOpen] = React.useState(false);
  const [addLoading, setAddLoading] = React.useState(false);
  const [newSite, setNewSite] = React.useState<NewSiteForm>(EMPTY_NEW_SITE);

  const addSite = React.useCallback(async () => {
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
        enabled: newSite.enabled,
        categories_tv: "5000",
        categories_movies: "2000",
        priority: 100,
      } as never,
    });
    setAddLoading(false);
    if (!error) {
      toast.success(`Added indexer site "${newSite.name}"`);
      setAddOpen(false);
      setNewSite(EMPTY_NEW_SITE);
      await qc.invalidateQueries({ queryKey: SITES_KEY });
    } else {
      toast.error("Failed to add site");
    }
  }, [newSite, qc]);

  const deleteSite = React.useCallback(
    async (siteId: string, siteName: string) => {
      const { error } = await apiClient.DELETE("/api/v1/indexers/sites/{site_id}", {
        params: { path: { site_id: siteId } },
      });
      if (!error) {
        toast.success(`Deleted "${siteName}"`);
        await qc.invalidateQueries({ queryKey: SITES_KEY });
      } else {
        toast.error("Failed to delete site");
      }
    },
    [qc],
  );

  // ── Edit dialog ─────────────────────────────────────────────────────────
  const [editOpen, setEditOpen] = React.useState(false);
  const [editLoading, setEditLoading] = React.useState(false);
  const [editSite, setEditSite] = React.useState<Site | null>(null);

  const openEdit = React.useCallback((site: Site) => {
    setEditSite({ ...site });
    setEditOpen(true);
  }, []);

  const saveEdit = React.useCallback(async () => {
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
        enabled: editSite.enabled,
        priority: Number.isFinite(editSite.priority) ? editSite.priority : 100,
      } as never,
    });
    setEditLoading(false);
    if (!error) {
      toast.success(`Updated "${editSite.name}"`);
      setEditOpen(false);
      setEditSite(null);
      await qc.invalidateQueries({ queryKey: SITES_KEY });
    } else {
      toast.error("Failed to update site");
    }
  }, [editSite, qc]);

  // ── Mirror management dialog ──────────────────────────────────────────────
  // Edits are staged locally (reorder / enable / add / delete) and committed as
  // one PUT of the full mirror list. The backend enforces the rules: seeded
  // mirrors can be reordered/disabled but not deleted; the active URL must stay
  // present and enabled.
  const [urlOpen, setUrlOpen] = React.useState(false);
  const [urlLoading, setUrlLoading] = React.useState(false);
  const [urlSite, setUrlSite] = React.useState<Site | null>(null);
  const [urlMirrors, setUrlMirrors] = React.useState<MirrorEntry[]>([]);
  const [urlActive, setUrlActive] = React.useState("");
  const [newUrl, setNewUrl] = React.useState("");

  const openUrls = React.useCallback((site: Site) => {
    setUrlSite(site);
    setUrlMirrors(siteMirrors(site).map((m) => ({ ...m })));
    setUrlActive(site.url);
    setNewUrl("");
    setUrlOpen(true);
  }, []);

  // Selecting an active mirror also enables it — the active URL cannot be off.
  const setActiveUrl = React.useCallback((url: string) => {
    setUrlActive(url);
    setUrlMirrors((prev) => prev.map((m) => (m.url === url ? { ...m, enabled: true } : m)));
  }, []);

  const toggleMirror = React.useCallback(
    (url: string) => {
      if (url === urlActive) {
        toast.error("Can't disable the active mirror — switch active first");
        return;
      }
      setUrlMirrors((prev) => prev.map((m) => (m.url === url ? { ...m, enabled: !m.enabled } : m)));
    },
    [urlActive],
  );

  const moveMirror = React.useCallback((index: number, dir: -1 | 1) => {
    setUrlMirrors((prev) => {
      const next = index + dir;
      if (next < 0 || next >= prev.length) return prev;
      const copy = [...prev];
      [copy[index], copy[next]] = [copy[next], copy[index]];
      return copy;
    });
  }, []);

  const addUrl = React.useCallback(() => {
    const trimmed = newUrl.trim();
    if (!trimmed) return;
    if (urlMirrors.some((m) => m.url === trimmed)) {
      toast.error("URL already exists");
      return;
    }
    setUrlMirrors((prev) => [...prev, { url: trimmed, enabled: true, source: "user" }]);
    setNewUrl("");
  }, [newUrl, urlMirrors]);

  const removeUrl = React.useCallback(
    (url: string) => {
      if (url === urlActive) {
        toast.error("Can't remove the active mirror");
        return;
      }
      const entry = urlMirrors.find((m) => m.url === url);
      if (entry?.source === "seeded") {
        toast.error("Built-in mirrors can't be deleted — disable it instead");
        return;
      }
      setUrlMirrors((prev) => prev.filter((m) => m.url !== url));
    },
    [urlActive, urlMirrors],
  );

  const saveUrls = React.useCallback(async () => {
    if (!urlSite) return;
    setUrlLoading(true);
    const { error } = await apiClient.PUT("/api/v1/indexers/sites/{site_id}", {
      params: { path: { site_id: urlSite.id } },
      body: { url: urlActive, mirrors: urlMirrors } as never,
    });
    setUrlLoading(false);
    if (!error) {
      toast.success("Mirrors updated");
      setUrlOpen(false);
      await qc.invalidateQueries({ queryKey: SITES_KEY });
    } else {
      const detail = (error as { detail?: string })?.detail;
      toast.error(detail ?? "Failed to update mirrors");
    }
  }, [urlSite, urlActive, urlMirrors, qc]);

  const invalidateSites = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: SITES_KEY });
  }, [qc]);

  return {
    qc,
    sitesQuery,
    sites,
    updateSite,
    deleteSite,
    invalidateSites,
    // add
    addOpen,
    setAddOpen,
    addLoading,
    newSite,
    setNewSite,
    addSite,
    // edit
    editOpen,
    setEditOpen,
    editLoading,
    editSite,
    setEditSite,
    openEdit,
    saveEdit,
    // mirrors
    urlOpen,
    setUrlOpen,
    urlLoading,
    urlSite,
    urlMirrors,
    urlActive,
    newUrl,
    setNewUrl,
    openUrls,
    setActiveUrl,
    toggleMirror,
    moveMirror,
    addUrl,
    removeUrl,
    saveUrls,
  };
}
