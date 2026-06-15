"use client";

import * as React from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import type { PreferenceMode } from "@/components/preference-multi-select";
import apiClient from "@/lib/api/client";
import { useQualityCodecOptions } from "@/hooks/use-quality-codec-options";

export type MediaKind = "show" | "movie";

type MediaInput = {
  id?: string | null;
  preferred_quality?: string[] | null;
  preferred_codec?: string[] | null;
  subtitle_languages?: string[] | null;
  continuous_download?: boolean | null;
  skipped?: boolean | null;
};

function toMode(value: string[] | null | undefined): PreferenceMode {
  if (value == null) return "default";
  if (value.length === 0) return "any";
  return "specific";
}

function bodyFor(mode: PreferenceMode, selected: string[]): string[] | null {
  if (mode === "default") return null;
  if (mode === "any") return [];
  return selected;
}

const ENDPOINTS = {
  show: {
    quality: "/api/v1/shows/{show_id}/preferred-quality",
    codec: "/api/v1/shows/{show_id}/preferred-codec",
    subtitleLanguages: "/api/v1/shows/{show_id}/subtitle-languages",
    continuousDownload: "/api/v1/shows/{show_id}/continuous-download",
    skip: "/api/v1/shows/{show_id}/skip",
    metadata: "/api/v1/shows/{show_id}/metadata",
    pathKey: "show_id",
    invalidateKey: "show",
  },
  movie: {
    quality: "/api/v1/movies/{movie_id}/preferred-quality",
    codec: "/api/v1/movies/{movie_id}/preferred-codec",
    subtitleLanguages: "/api/v1/movies/{movie_id}/subtitle-languages",
    continuousDownload: "/api/v1/movies/{movie_id}/continuous-download",
    skip: "/api/v1/movies/{movie_id}/skip",
    metadata: "/api/v1/movies/{movie_id}/metadata",
    pathKey: "movie_id",
    invalidateKey: "movie",
  },
} as const;

export function useMediaPreferences(media: MediaInput, kind: MediaKind) {
  const queryClient = useQueryClient();
  const cfg = ENDPOINTS[kind];

  const optionsQuery = useQualityCodecOptions();

  const enabledQualityNames = React.useMemo(
    () =>
      (optionsQuery.data?.qualityOptions ?? []).flatMap((o) =>
        o.enabled !== false ? [o.name] : [],
      ),
    [optionsQuery.data?.qualityOptions],
  );
  const enabledCodecNames = React.useMemo(
    () =>
      (optionsQuery.data?.codecOptions ?? []).flatMap((o) => (o.enabled !== false ? [o.name] : [])),
    [optionsQuery.data?.codecOptions],
  );

  const [qualityMode, setQualityMode] = React.useState<PreferenceMode>(
    toMode(media.preferred_quality),
  );
  const [qualitySelected, setQualitySelected] = React.useState<string[]>(
    Array.isArray(media.preferred_quality) ? media.preferred_quality : [],
  );
  const [codecMode, setCodecMode] = React.useState<PreferenceMode>(toMode(media.preferred_codec));
  const [codecSelected, setCodecSelected] = React.useState<string[]>(
    Array.isArray(media.preferred_codec) ? media.preferred_codec : [],
  );
  const [subtitleLanguages, setSubtitleLanguages] = React.useState<string[]>(
    Array.isArray(media.subtitle_languages) ? media.subtitle_languages : [],
  );
  const [refreshing, setRefreshing] = React.useState(false);

  const id = media.id!;

  const invalidate = React.useCallback(
    () => queryClient.invalidateQueries({ queryKey: [cfg.invalidateKey, id] }),
    [queryClient, cfg.invalidateKey, id],
  );

  const params = React.useMemo(
    () => ({ path: { [cfg.pathKey]: id } as Record<string, string> }),
    [cfg.pathKey, id],
  );

  const saveQuality = React.useCallback(
    async (mode: PreferenceMode, selected: string[]) => {
      setQualityMode(mode);
      setQualitySelected(selected);
      const { error } = await apiClient.POST(cfg.quality, {
        params,
        body: { preferred_quality: bodyFor(mode, selected) },
      } as never);
      if (error) toast.error("Failed to update quality preference");
      else {
        toast.success("Quality preference updated");
        await invalidate();
      }
    },
    [cfg.quality, params, invalidate],
  );

  const saveCodec = React.useCallback(
    async (mode: PreferenceMode, selected: string[]) => {
      setCodecMode(mode);
      setCodecSelected(selected);
      const { error } = await apiClient.POST(cfg.codec, {
        params,
        body: { preferred_codec: bodyFor(mode, selected) },
      } as never);
      if (error) toast.error("Failed to update codec preference");
      else {
        toast.success("Codec preference updated");
        await invalidate();
      }
    },
    [cfg.codec, params, invalidate],
  );

  const saveSubtitleLanguages = React.useCallback(
    async (langs: string[]) => {
      setSubtitleLanguages(langs);
      const { error } = await apiClient.POST(cfg.subtitleLanguages, {
        params,
        body: langs.length > 0 ? langs : null,
      } as never);
      if (error) toast.error("Failed to update subtitle languages");
      else {
        toast.success("Subtitle languages updated");
        await invalidate();
      }
    },
    [cfg.subtitleLanguages, params, invalidate],
  );

  const saveContinuousDownload = React.useCallback(
    async (v: string) => {
      const continuous_download = v === "null" ? null : v === "true";
      const { error } = await apiClient.POST(cfg.continuousDownload, {
        params: {
          path: { [cfg.pathKey]: id },
          query: { continuous_download },
        },
      } as never);
      if (error) toast.error("Failed to update continuous download");
      else await invalidate();
    },
    [cfg.continuousDownload, cfg.pathKey, id, invalidate],
  );

  const toggleSkipped = React.useCallback(async () => {
    const { error } = await apiClient.POST(cfg.skip, {
      params: { path: { [cfg.pathKey]: id }, query: { skipped: !media.skipped } },
    } as never);
    if (error) toast.error("Failed to update skip status");
    else {
      const label = kind === "movie" ? "Movie" : "Show";
      toast.success(media.skipped ? `${label} marked as wanted` : `${label} marked as skipped`);
      await invalidate();
    }
  }, [cfg.skip, cfg.pathKey, id, media.skipped, kind, invalidate]);

  const refreshMetadata = React.useCallback(async () => {
    setRefreshing(true);
    try {
      const { error } = await apiClient.POST(cfg.metadata, { params } as never);
      if (error) toast.error("Failed to refresh metadata");
      else {
        toast.success("Metadata refreshed");
        await invalidate();
      }
    } finally {
      setRefreshing(false);
    }
  }, [cfg.metadata, params, invalidate]);

  return {
    enabledQualityNames,
    enabledCodecNames,
    qualityMode,
    qualitySelected,
    codecMode,
    codecSelected,
    subtitleLanguages,
    refreshing,
    saveQuality,
    saveCodec,
    saveSubtitleLanguages,
    saveContinuousDownload,
    toggleSkipped,
    refreshMetadata,
  };
}
