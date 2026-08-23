"use client";

import * as React from "react";
import { CheckIcon, ChevronsUpDownIcon } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import apiClient from "@/lib/api/client";
import { useLibraries, type LibraryItem } from "@/hooks/use-libraries";
import type { components } from "@/lib/api/api";

type Media = components["schemas"]["PublicShow"] | components["schemas"]["PublicMovie"];

export function LibraryCombobox({
  media,
  mediaType,
}: {
  media: Media;
  mediaType: "show" | "movie";
}) {
  const queryClient = useQueryClient();
  const listboxId = React.useId();
  const [open, setOpen] = React.useState(false);
  const [value, setValue] = React.useState<string>(
    !media.library || media.library === "" ? "Default" : media.library,
  );

  const librariesQuery = useLibraries(mediaType);
  const loadError = librariesQuery.isError ? "Failed to load libraries" : null;
  const libraries = React.useMemo(() => {
    const list = librariesQuery.data ?? [];
    const others = list.filter((l) => l.name !== "Default");
    const defaultItem =
      list.find((l) => l.name === "Default") ??
      ({ name: "Default", path: "Default" } as LibraryItem);
    return [defaultItem, ...others];
  }, [librariesQuery.data]);

  async function handleSelect(libraryName: string) {
    setValue(libraryName);
    setOpen(false);
    let res;
    if (mediaType === "show") {
      res = await apiClient.POST("/api/v1/shows/{show_id}/library", {
        params: {
          path: { show_id: media.id! },
          query: { library: libraryName },
        },
      });
    } else {
      res = await apiClient.POST("/api/v1/movies/{movie_id}/library", {
        params: {
          path: { movie_id: media.id! },
          query: { library: libraryName },
        },
      });
    }
    if (res.error) {
      toast.error("Failed to update library");
    } else {
      toast.success(`Library updated to ${libraryName}`);
      await queryClient.invalidateQueries({
        queryKey: [mediaType === "show" ? "show" : "movie", media.id],
      });
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            className="flex-1 justify-between"
            role="combobox"
            aria-expanded={open}
            aria-controls={listboxId}
          />
        }
      >
        {value || "Select Library"}
        <ChevronsUpDownIcon className="opacity-50" />
      </PopoverTrigger>
      <PopoverContent className="w-[200px] p-0">
        <Command id={listboxId}>
          <CommandInput placeholder="Search library..." />
          <CommandList>
            {loadError ? (
              <p className="p-2 text-sm text-muted-foreground">{loadError}</p>
            ) : (
              <>
                <CommandEmpty>No library found.</CommandEmpty>
                <CommandGroup>
                  {libraries.map((item) => (
                    <CommandItem
                      key={item.name}
                      value={item.name}
                      onSelect={() => handleSelect(item.name)}
                    >
                      <CheckIcon
                        className={cn("mr-2", value === item.name ? "opacity-100" : "opacity-0")}
                      />
                      {item.name}
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
