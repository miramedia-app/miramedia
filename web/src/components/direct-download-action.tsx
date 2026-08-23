"use client";

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { mediaStreamDownloadUrl, type MediaStreamKind } from "@/lib/media-download";
import { cn } from "@/lib/utils";

export function DirectDownloadAction({
  mediaType,
  mediaId,
  fileId,
  buttonVariant = "outline",
  buttonSize = "sm",
  buttonClassName,
  triggerLabel,
}: {
  mediaType: MediaStreamKind;
  mediaId: string;
  fileId: string;
  buttonVariant?: "outline" | "ghost" | "default";
  buttonSize?: "sm" | "default" | "icon";
  buttonClassName?: string;
  triggerLabel?: string;
}) {
  const href = mediaStreamDownloadUrl({ mediaType, mediaId, fileId });
  const label = triggerLabel ?? "Download";

  return (
    <Button
      variant={buttonVariant}
      size={buttonSize}
      className={cn(
        buttonSize === "icon" ? "text-muted-foreground" : undefined,
        buttonSize === "icon" && !buttonClassName ? "h-7 w-7" : undefined,
        buttonClassName,
      )}
      nativeButton={false}
      render={<a href={href} />}
      aria-label={label}
    >
      <Download className={buttonSize === "icon" ? "size-4" : "h-3.5 w-3.5"} />
      {buttonSize !== "icon" ? <span>{label}</span> : null}
    </Button>
  );
}
