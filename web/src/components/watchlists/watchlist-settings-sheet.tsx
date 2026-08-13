"use client";

import * as React from "react";
import { Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useUpdateWatchlist } from "@/hooks/use-watchlists";

export function canSaveWatchlistSettings(name: string): boolean {
  return name.trim().length > 0;
}

export function WatchlistSettingsSheet({
  watchlistId,
  name: initialName,
  description: initialDescription,
}: {
  watchlistId: string;
  name: string;
  description: string | null;
}) {
  const updateWatchlist = useUpdateWatchlist();

  const [name, setName] = React.useState(initialName);
  const [description, setDescription] = React.useState(initialDescription ?? "");

  React.useEffect(() => {
    setName(initialName);
    setDescription(initialDescription ?? "");
  }, [initialName, initialDescription]);

  async function handleSave(event: React.FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!canSaveWatchlistSettings(trimmedName)) return;
    try {
      await updateWatchlist.mutateAsync({
        watchlistId,
        body: {
          name: trimmedName,
          description: description.trim() ? description.trim() : null,
        },
      });
    } catch {
      // Toasts are handled in mutation hooks.
    }
  }

  return (
    <Sheet>
      <SheetTrigger render={<Button variant="outline" size="sm" />}>
        <Settings className="size-4" />
        Settings
      </SheetTrigger>
      <SheetContent side="right" className="w-80 overflow-y-auto sm:max-w-sm">
        <SheetHeader>
          <SheetTitle>Watchlist Settings</SheetTitle>
          <SheetDescription>{initialName}</SheetDescription>
        </SheetHeader>

        <form onSubmit={handleSave} className="flex flex-col gap-6 px-4 py-6">
          <div className="grid gap-2">
            <Label htmlFor="watchlist-settings-name">Name</Label>
            <Input
              id="watchlist-settings-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="watchlist-settings-description">Description</Label>
            <Textarea
              id="watchlist-settings-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
            />
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={updateWatchlist.isPending || !canSaveWatchlistSettings(name)}
          >
            Save
          </Button>
        </form>
      </SheetContent>
    </Sheet>
  );
}
