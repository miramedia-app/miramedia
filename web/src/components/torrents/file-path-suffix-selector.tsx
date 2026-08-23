"use client";

import * as React from "react";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { formatFileSuffix, saveDirectoryPreview } from "@/lib/utils";
import { useLibraries } from "@/hooks/use-libraries";
import type { components } from "@/lib/api/api";

type Quality = "uhd" | "fullhd" | "hd" | "sd" | "unknown";
type Media =
  | components["schemas"]["Movie"]
  | components["schemas"]["Show"]
  | components["schemas"]["PublicMovie"]
  | components["schemas"]["PublicShow"];

export function FilePathSuffixSelector({
  media,
  mediaType,
  quality,
  onQualityChange,
  variant,
  onVariantChange,
  library,
  onLibraryChange,
  showQuality = true,
}: {
  media: Media;
  mediaType?: "show" | "movie";
  quality: Quality;
  onQualityChange: (v: Quality) => void;
  variant: string;
  onVariantChange: (v: string) => void;
  library?: string;
  onLibraryChange?: (v: string) => void;
  showQuality?: boolean;
}) {
  const inferredType: "show" | "movie" = mediaType ?? ("seasons" in media ? "show" : "movie");

  const librariesQuery = useLibraries(inferredType);
  const libraries = librariesQuery.data ?? [];
  const loadError = librariesQuery.isError ? "Failed to load libraries" : null;

  // Memoize so the (cheap but allocating) string builds don't re-run for
  // every other state change in the parent dialog.
  const previewSuffix = React.useMemo(
    () => formatFileSuffix({ quality, variant }),
    [quality, variant],
  );

  // Initialize library exactly once when the caller hasn't seeded it. A ref
  // guard prevents the effect from clobbering a user-edited library if the
  // `media` prop later changes identity (e.g. dialog candidate switch).
  const libraryInitedRef = React.useRef(false);
  React.useEffect(() => {
    if (libraryInitedRef.current) return;
    if (library === undefined && onLibraryChange) {
      onLibraryChange((media as { library?: string | null }).library ?? "Default");
      libraryInitedRef.current = true;
    }
  }, [library, onLibraryChange, media]);

  // Cast media to the union expected by saveDirectoryPreview (Show/Movie)
  // Both PublicShow/PublicMovie have the required fields at runtime; saveDirectoryPreview just reads name/year/etc.
  const previewMedia = media as components["schemas"]["Show"] | components["schemas"]["Movie"];
  const previewPath = React.useMemo(
    () => saveDirectoryPreview(previewMedia, previewSuffix),
    [previewMedia, previewSuffix],
  );

  return (
    <div className="grid w-full items-center gap-3">
      {showQuality && (
        <div className="grid w-full items-center gap-1.5">
          <Label htmlFor="quality-select">Quality</Label>
          <Select value={quality} onValueChange={(v) => onQualityChange(v as Quality)}>
            <SelectTrigger id="quality-select" className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="unknown">unknown (auto)</SelectItem>
              <SelectItem value="uhd">2160p (UHD)</SelectItem>
              <SelectItem value="fullhd">1080p (Full HD)</SelectItem>
              <SelectItem value="hd">720p (HD)</SelectItem>
              <SelectItem value="sd">480p (SD)</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            Auto-detected from the torrent title; override here only if wrong.
          </p>
        </div>
      )}

      <div className="grid w-full items-center gap-1.5">
        <Label htmlFor="variant">
          Variant <span className="text-muted-foreground">(optional)</span>
        </Label>
        <Input
          type="text"
          id="variant"
          className="max-w-sm"
          value={variant}
          onChange={(e) => onVariantChange(e.target.value)}
          placeholder="e.g. director-cut, remux, imax"
        />
        <p className="text-xs text-muted-foreground">
          Free-text differentiator for multiple versions at the same quality. Codec, HDR and source
          are auto-detected.
        </p>
      </div>

      {loadError ? (
        <p className="text-sm text-muted-foreground">{loadError}</p>
      ) : (
        libraries.length > 0 &&
        onLibraryChange && (
          <div className="grid w-full items-center gap-1.5">
            <Label htmlFor="library-select">Library</Label>
            <Select value={library ?? "Default"} onValueChange={onLibraryChange}>
              <SelectTrigger id="library-select" className="w-[240px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Default">Default</SelectItem>
                {libraries.map((lib) => (
                  <SelectItem key={lib.name} value={lib.name}>
                    {lib.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )
      )}

      <div>
        <Label htmlFor="file-suffix-display">
          The files will be saved in the following directory:
        </Label>
        <p className="text-sm text-muted-foreground" id="file-suffix-display">
          {previewPath}
        </p>
      </div>
    </div>
  );
}
