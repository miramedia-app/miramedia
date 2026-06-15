import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PageLoaderProps extends React.ComponentProps<"div"> {
  /** Text shown beneath the spinner. Defaults to "Loading…". */
  label?: string;
  /** Render full-viewport-height centered (for top-level gates). */
  fullscreen?: boolean;
}

/**
 * Centered spinner + label used for page / section loading states.
 * Keeps loading UI consistent and visually polished across the app.
 */
export function PageLoader({
  label = "Loading…",
  fullscreen = false,
  className,
  ...rest
}: PageLoaderProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={cn(
        "flex w-full flex-col items-center justify-center gap-3 text-muted-foreground",
        fullscreen ? "min-h-svh" : "py-16",
        className,
      )}
      {...rest}
    >
      <div className="relative flex items-center justify-center">
        <span className="absolute inline-flex h-10 w-10 animate-ping rounded-full bg-primary/15" />
        <Loader2 className="relative size-7 animate-spin text-primary" />
      </div>
      <span className="animate-pulse text-sm font-medium tracking-wide">{label}</span>
    </div>
  );
}
