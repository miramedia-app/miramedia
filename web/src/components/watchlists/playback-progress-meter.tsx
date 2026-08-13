import { Progress } from "@/components/ui/progress";
import { formatPlaybackProgressMeter } from "@/lib/watchlists";

export function PlaybackProgressMeter({
  positionMs,
  durationMs,
}: {
  positionMs: number;
  durationMs?: number | null;
}) {
  const copy = formatPlaybackProgressMeter(positionMs, durationMs);
  if (!copy) return null;

  return (
    <div
      className="mr-5 flex w-48 min-w-0 shrink-0 flex-col gap-1"
      aria-label={`${copy.elapsed} of ${copy.duration}, ${copy.percent}% watched`}
    >
      <Progress value={copy.percent} className="h-1.5 w-full" />
      <div className="grid w-full min-w-0 grid-cols-3 items-center text-[11px] text-muted-foreground tabular-nums">
        <span className="truncate text-start" title="Watched">
          {copy.elapsed}
        </span>
        <span className="truncate text-center" title="Remaining">
          {copy.remaining}
        </span>
        <span className="truncate text-end">{copy.percent}%</span>
      </div>
    </div>
  );
}
