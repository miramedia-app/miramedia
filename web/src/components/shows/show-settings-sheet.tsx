"use client";

import { Settings } from "lucide-react";
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

type Show = components["schemas"]["PublicShow"];

export function ShowSettingsSheet({ show }: { show: Show }) {
  const prefs = useMediaPreferences(show, "show");

  return (
    <Sheet>
      <SheetTrigger render={<Button variant="outline" size="sm" />}>
        <Settings className="h-4 w-4" />
        Settings
      </SheetTrigger>
      <SheetContent side="right" className="w-80 overflow-y-auto sm:max-w-sm">
        <SheetHeader>
          <SheetTitle>Show Settings</SheetTitle>
          <SheetDescription>{show.name}</SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 px-4 py-6">
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

          <div className="flex flex-col gap-2">
            <Label>Subtitle Languages</Label>
            <LanguageMultiCombobox
              value={prefs.subtitleLanguages}
              onChange={prefs.saveSubtitleLanguages}
              placeholder="Add a language"
            />
            <p className="text-xs text-muted-foreground">Leave empty to use global default.</p>
          </div>

          <Separator />

          <div className="flex flex-col gap-2">
            <Label>Continuous Download</Label>
            <Select
              value={show.continuous_download == null ? "null" : String(show.continuous_download)}
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
            <p className="text-xs text-muted-foreground">Auto-download new episodes as they air.</p>
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <Label>Skip this show</Label>
              <p className="text-xs text-muted-foreground">Prevent automatic downloads.</p>
            </div>
            <Switch checked={!!show.skipped} onCheckedChange={prefs.toggleSkipped} />
          </div>

          <Separator />

          <div className="flex flex-col gap-2">
            <Label>Library</Label>
            <div className="flex items-center gap-2">
              <LibraryCombobox media={show} mediaType="show" />
              <MoveLibraryButton
                mediaId={show.id!}
                mediaType="show"
                currentLibrary={show.library || "Default"}
              />
            </div>
          </div>

          <Separator />

          <div className="flex flex-col gap-2">
            <Label>Metadata</Label>
            <Button variant="outline" onClick={prefs.refreshMetadata} disabled={prefs.refreshing}>
              {prefs.refreshing ? "Refreshing..." : "Refresh Metadata"}
            </Button>
            <p className="text-xs text-muted-foreground">
              Re-fetch title, overview, and episode list.
            </p>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
