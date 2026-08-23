"use client";

import * as React from "react";
import { Settings, RefreshCw } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { LanguageMultiCombobox } from "@/components/ui/language-multi-combobox";
import { LibraryCombobox } from "@/components/library-combobox";
import { MoveLibraryButton } from "@/components/move-library-button";
import { PreferenceMultiSelect } from "@/components/preference-multi-select";
import { useMediaPreferences } from "@/hooks/use-media-preferences";
import type { components } from "@/lib/api/api";

type Movie = components["schemas"]["PublicMovie"];

export function MovieSettingsSheet({ movie }: { movie: Movie }) {
  const [open, setOpen] = React.useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger render={<Button variant="outline" size="sm" />}>
        <Settings className="h-4 w-4" />
        Settings
      </SheetTrigger>
      <SheetContent side="right" className="w-80 overflow-y-auto sm:max-w-sm">
        {open ? <MovieSettingsBody movie={movie} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function MovieSettingsBody({ movie }: { movie: Movie }) {
  const prefs = useMediaPreferences(movie, "movie");

  return (
    <>
      <SheetHeader>
        <SheetTitle>Movie Settings</SheetTitle>
        <SheetDescription>{movie.name}</SheetDescription>
      </SheetHeader>

      <div className="flex flex-col gap-6 px-4 py-6">
        {prefs.loadError ? (
          <p className="text-sm text-muted-foreground">{prefs.loadError}</p>
        ) : (
          <>
            <PreferenceMultiSelect
              label="Preferred Quality"
              mode={prefs.qualityMode}
              selected={prefs.qualitySelected}
              options={prefs.enabledQualityNames}
              onChange={prefs.saveQuality}
              description="Use global default, accept Any, or restrict to specific qualities."
            />

            <PreferenceMultiSelect
              label="Preferred Codec"
              mode={prefs.codecMode}
              selected={prefs.codecSelected}
              options={prefs.enabledCodecNames}
              onChange={prefs.saveCodec}
              description="Use global default, accept Any, or restrict to specific codecs."
            />
          </>
        )}

        <div className="flex flex-col gap-2">
          <Label>Subtitle Languages</Label>
          <LanguageMultiCombobox
            value={prefs.subtitleLanguages}
            onChange={prefs.saveSubtitleLanguages}
            placeholder="Add a language"
          />
          <p className="text-xs text-muted-foreground">Leave empty to use global default.</p>
        </div>

        <div className="flex flex-col gap-2">
          <Label>Continuous Download</Label>
          <Select
            value={movie.continuous_download == null ? "null" : String(movie.continuous_download)}
            onValueChange={prefs.saveContinuousDownload}
          >
            <SelectTrigger className="w-full">
              <SelectValue>
                {(v) => (v === "true" ? "On" : v === "false" ? "Off" : "Use global default")}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="null">Use global default</SelectItem>
              <SelectItem value="true">On</SelectItem>
              <SelectItem value="false">Off</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">Auto-download this movie when available.</p>
        </div>

        <Separator />

        <div className="flex items-center justify-between gap-3">
          <div>
            <Label>Skip this movie</Label>
            <p className="text-xs text-muted-foreground">Prevent automatic downloads.</p>
          </div>
          <Switch checked={!!movie.skipped} onCheckedChange={prefs.toggleSkipped} />
        </div>

        <Separator />

        <div className="flex flex-col gap-2">
          <Label>Library</Label>
          <div className="flex items-center gap-2">
            <LibraryCombobox media={movie} mediaType="movie" />
            <MoveLibraryButton
              mediaId={movie.id!}
              mediaType="movie"
              currentLibrary={movie.library || "Default"}
            />
          </div>
        </div>

        <Separator />

        <div className="flex flex-col gap-2">
          <Label>Refresh Metadata</Label>
          <p className="text-xs text-muted-foreground">
            Re-fetch title, overview, rating, and poster from the metadata provider.
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={prefs.refreshing}
            onClick={prefs.refreshMetadata}
          >
            <RefreshCw className={`h-4 w-4 ${prefs.refreshing ? "animate-spin" : ""}`} />
            {prefs.refreshing ? "Refreshing…" : "Refresh Metadata"}
          </Button>
        </div>
      </div>
    </>
  );
}
