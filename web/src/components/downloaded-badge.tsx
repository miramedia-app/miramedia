import { Check } from "lucide-react";

type DownloadedBadgeProps = {
  /** True when the media is fully downloaded. */
  complete: boolean;
  /** Episodes downloaded so far (shows only). Omit for movies. */
  downloaded?: number;
  /** Total wanted episodes (shows only). Omit for movies. */
  total?: number;
};

/**
 * Frosted-glass indicator overlaid on a poster's top-right corner.
 * Monochrome to match the app theme (no accent color). Both states are
 * 24px tall so they align across the grid.
 *
 * - Fully downloaded  -> frosted circle with a bold check.
 * - Partial (shows)   -> frosted pill "x / y".
 * - Nothing yet       -> renders nothing, keeping the poster clean.
 */
export function DownloadedBadge({ complete, downloaded, total }: DownloadedBadgeProps) {
  const frost =
    "absolute top-2 right-2 z-10 flex h-6 items-center justify-center bg-black/40 text-white shadow-lg ring-1 ring-white/25 backdrop-blur-md";

  if (complete) {
    return (
      <div
        className={`${frost} w-6 rounded-full`}
        title="Fully downloaded"
        aria-label="Fully downloaded"
      >
        <Check className="h-3.5 w-3.5 drop-shadow" strokeWidth={3.5} />
      </div>
    );
  }

  const hasProgress = typeof downloaded === "number" && typeof total === "number" && downloaded > 0;
  if (!hasProgress) return null;

  return (
    <div
      className={`${frost} rounded-full px-2 text-[11px] leading-none font-semibold`}
      title={`${downloaded} of ${total} episodes downloaded`}
      aria-label={`${downloaded} of ${total} episodes downloaded`}
    >
      <span className="tabular-nums">
        {downloaded}
        <span className="px-0.5 text-white/50">/</span>
        {total}
      </span>
    </div>
  );
}
