"use client";

import { useState } from "react";
import { ImageOff } from "lucide-react";
import { getFullyQualifiedMediaName } from "@/lib/utils";

type MediaShape = { id?: string | null; name: string; year: number | null };

export function MediaPicture({
  media,
  priority = false,
}: {
  media: MediaShape;
  /** Set on the LCP image (e.g. the hero poster on a detail page). */
  priority?: boolean;
}) {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
  const base = `${apiUrl}/api/v1/static/image/${media.id}`;
  const srcSet = (ext: string) =>
    [200, 300, 400, 600].map((w) => `${base}.${ext}?w=${w} ${w}w`).join(", ");
  const [broken, setBroken] = useState(false);

  if (media.id == null || broken) {
    return (
      <div
        className="flex h-full w-full items-center justify-center rounded-lg bg-muted"
        style={{ aspectRatio: "2 / 3" }}
        role="img"
        aria-label={`${getFullyQualifiedMediaName(media)}'s Poster Image`}
      >
        <ImageOff className="h-12 w-12 text-muted-foreground" />
      </div>
    );
  }

  return (
    <picture>
      <source
        srcSet={srcSet("avif")}
        type="image/avif"
        sizes="(min-width: 1536px) 20vw, (min-width: 1280px) 25vw, (min-width: 1024px) 33vw, (min-width: 768px) 50vw, 100vw"
      />
      <source
        srcSet={srcSet("webp")}
        type="image/webp"
        sizes="(min-width: 1536px) 20vw, (min-width: 1280px) 25vw, (min-width: 1024px) 33vw, (min-width: 768px) 50vw, 100vw"
      />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        alt={`${getFullyQualifiedMediaName(media)}'s Poster Image`}
        className="h-full w-full rounded-lg object-cover"
        src={`${base}.jpeg`}
        srcSet={srcSet("jpeg")}
        sizes="(min-width: 1536px) 20vw, (min-width: 1280px) 25vw, (min-width: 1024px) 33vw, (min-width: 768px) 50vw, 100vw"
        // Intrinsic dims + aspect-ratio prevent CLS on grid placement.
        width={200}
        height={300}
        style={{ aspectRatio: "2 / 3" }}
        loading={priority ? "eager" : "lazy"}
        decoding="async"
        fetchPriority={priority ? "high" : "auto"}
        onError={() => setBroken(true)}
      />
    </picture>
  );
}
