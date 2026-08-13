"use client";

import * as React from "react";
import { ListPlus, LoaderCircle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useFeatures } from "@/components/providers/features-provider";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import {
  EMPTY_WATCHLISTS,
  useAddToWatchlist,
  useCreateWatchlist,
  useWatchlists,
} from "@/hooks/use-watchlists";
import { watchlistOverflowActionsEnabled, type WatchlistMediaKind } from "@/lib/watchlists";

export function AddToWatchlistMenuItem({ onSelect }: { onSelect: () => void }) {
  const { watchlists, custom_lists } = useFeatures();
  const { addToWatchlist: addEnabled } = watchlistOverflowActionsEnabled({
    watchlists,
    custom_lists,
  });
  if (!addEnabled) return null;
  return (
    <DropdownMenuItem onClick={onSelect}>
      <ListPlus className="size-4" />
      Add to Watchlist
    </DropdownMenuItem>
  );
}

export function normalizeWatchlistName(value: string): string {
  return value.trim();
}

export function validateWatchlistName(value: string): string | null {
  return normalizeWatchlistName(value) ? null : "Name is required";
}

export function watchlistSelectLabel(
  lists: { id: string; name: string }[],
  selectedId: string,
): string {
  if (!selectedId) return "Select a list";
  return lists.find((list) => list.id === selectedId)?.name ?? "Select a list";
}

export function AddToWatchlist({
  mediaKind,
  mediaId,
  triggerLabel = "Add to Watchlist",
  buttonVariant = "outline",
  buttonSize = "sm",
  menuItem = false,
  open: controlledOpen,
  onOpenChange,
  hideTrigger = false,
}: {
  mediaKind: WatchlistMediaKind;
  mediaId: string;
  triggerLabel?: string;
  buttonVariant?: "outline" | "ghost" | "default";
  buttonSize?: "sm" | "default";
  menuItem?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  hideTrigger?: boolean;
}) {
  const { watchlists, custom_lists } = useFeatures();
  const [internalOpen, setInternalOpen] = React.useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;
  const [selectedListId, setSelectedListId] = React.useState<string>("");
  const [createMode, setCreateMode] = React.useState(false);
  const [newListName, setNewListName] = React.useState("");

  const listsEnabled = watchlistOverflowActionsEnabled({
    watchlists,
    custom_lists,
  }).addToWatchlist;
  const listsQuery = useWatchlists(listsEnabled);
  const createWatchlist = useCreateWatchlist();
  const addToWatchlist = useAddToWatchlist();

  const lists = listsQuery.data ?? EMPTY_WATCHLISTS;
  const pending = createWatchlist.isPending || addToWatchlist.isPending;

  React.useEffect(() => {
    if (!open || !listsEnabled) return;
    if (lists.length > 0 && !selectedListId) {
      setSelectedListId(lists[0]!.id);
    }
  }, [open, lists, selectedListId, listsEnabled]);

  if (!listsEnabled) return null;

  function resetState() {
    setCreateMode(false);
    setNewListName("");
    setSelectedListId(lists[0]?.id ?? "");
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    try {
      let watchlistId = selectedListId;
      if (createMode) {
        const validation = validateWatchlistName(newListName);
        if (validation) {
          toast.error(validation);
          return;
        }
        const created = await createWatchlist.mutateAsync({
          name: normalizeWatchlistName(newListName),
        });
        watchlistId = created.id;
      }
      if (!watchlistId) {
        toast.error("Choose a watchlist");
        return;
      }
      await addToWatchlist.mutateAsync({
        watchlistId,
        body: { media_kind: mediaKind, media_id: mediaId },
      });
      setOpen(false);
      resetState();
    } catch {
      // Toasts are handled in mutation hooks.
    }
  }

  const trigger = menuItem ? (
    <Button type="button" variant="ghost" size="sm" className="w-full justify-start">
      <ListPlus className="mr-2 h-4 w-4" />
      {triggerLabel}
    </Button>
  ) : (
    <Button type="button" variant={buttonVariant} size={buttonSize}>
      <ListPlus className="h-4 w-4" />
      {triggerLabel}
    </Button>
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) resetState();
      }}
    >
      {hideTrigger ? null : <DialogTrigger render={trigger} />}
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Add to Watchlist</DialogTitle>
            <DialogDescription>Choose an existing list or create one inline.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            {!createMode ? (
              <div className="grid gap-2">
                <Label htmlFor="watchlist-picker">Watchlist</Label>
                <Select value={selectedListId} onValueChange={setSelectedListId}>
                  <SelectTrigger id="watchlist-picker" className="w-full">
                    {watchlistSelectLabel(lists, selectedListId)}
                  </SelectTrigger>
                  <SelectContent>
                    {lists.map((list) => (
                      <SelectItem key={list.id} value={list.id}>
                        {list.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant="link"
                  className="h-auto justify-start px-0"
                  onClick={() => setCreateMode(true)}
                >
                  Create a new list
                </Button>
              </div>
            ) : (
              <div className="grid gap-2">
                <Label htmlFor="new-watchlist-name">New list name</Label>
                <Input
                  id="new-watchlist-name"
                  value={newListName}
                  onChange={(event) => setNewListName(event.target.value)}
                  autoFocus
                />
                {lists.length > 0 ? (
                  <Button
                    type="button"
                    variant="link"
                    className="h-auto justify-start px-0"
                    onClick={() => setCreateMode(false)}
                  >
                    Choose an existing list
                  </Button>
                ) : null}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={pending || listsQuery.isPending}>
              {pending ? (
                <>
                  <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                  Saving...
                </>
              ) : (
                "Add"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
