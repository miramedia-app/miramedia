"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import apiClient from "@/lib/api/client";
import type { Site } from "@/lib/indexers";

const SITES_KEY = ["indexers", "sites"];

export interface NewSiteForm {
  name: string;
  url: string;
  api_key: string;
  supports_tv: boolean;
  supports_movies: boolean;
  cloudflare_protected: boolean;
}

const EMPTY_NEW_SITE: NewSiteForm = {
  name: "",
  url: "",
  api_key: "",
  supports_tv: true,
  supports_movies: true,
  cloudflare_protected: false,
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
      const { data } = await apiClient.GET("/api/v1/indexers/sites", { signal });
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

  // ── URL management dialog ─────────────────────────────────────────────────
  const [urlOpen, setUrlOpen] = React.useState(false);
  const [urlLoading, setUrlLoading] = React.useState(false);
  const [urlSite, setUrlSite] = React.useState<Site | null>(null);
  const [newUrl, setNewUrl] = React.useState("");

  const openUrls = React.useCallback((site: Site) => {
    setUrlSite({ ...site, available_urls: [...(site.available_urls ?? [])] });
    setNewUrl("");
    setUrlOpen(true);
  }, []);

  const switchActiveUrl = React.useCallback(
    async (url: string) => {
      if (!urlSite) return;
      setUrlLoading(true);
      await updateSite(urlSite.id, { url });
      setUrlSite({ ...urlSite, url });
      setUrlLoading(false);
      toast.success("Active URL changed");
    },
    [urlSite, updateSite],
  );

  const addUrl = React.useCallback(async () => {
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
  }, [urlSite, newUrl, updateSite]);

  const removeUrl = React.useCallback(
    async (url: string) => {
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
    },
    [urlSite, updateSite],
  );

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
    // urls
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
  };
}
