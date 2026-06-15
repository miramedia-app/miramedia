import type * as React from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type StatusTone = "default" | "secondary" | "outline" | "destructive";

/**
 * Canonical status -> Badge variant map, shared by every page so the same
 * status reads identically everywhere. Monochrome only (no green/amber) to
 * keep the theme grayscale — tone, not color, conveys meaning:
 *
 * - default (solid)   complete / success: downloaded, imported, approved, finished, active
 * - secondary (solid) in progress / inactive: pending, downloading, wanted, skipped, paused, ambiguous
 * - destructive       error: failed*, rejected
 */
const STATUS_VARIANT: Record<string, StatusTone> = {
  downloaded: "default",
  imported: "default",
  approved: "default",
  finished: "default",
  active: "default",
  enabled: "default",
  healthy: "default",
  verified: "default",
  pending: "secondary",
  downloading: "secondary",
  wanted: "secondary",
  skipped: "secondary",
  paused: "secondary",
  ambiguous: "secondary",
  inactive: "secondary",
  disabled: "secondary",
  unverified: "secondary",
  unknown: "secondary",
  error: "destructive",
  critical: "destructive",
  failed: "destructive",
  failed_no_match: "destructive",
  failed_io: "destructive",
  rejected: "destructive",
  warning: "secondary",
  info: "secondary",
  debug: "secondary",
};

export function statusVariant(status: string): StatusTone {
  return STATUS_VARIANT[status.toLowerCase()] ?? "secondary";
}

const UNDERSCORES = /_/g;

function defaultLabel(status: string): string {
  const s = status.replace(UNDERSCORES, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Shared status pill. Pass a raw status string; variant and label are derived
 * from {@link STATUS_VARIANT}. Override `variant` for page-specific nuance
 * (e.g. a finished download whose import is still pending) and `label` for a
 * custom display string.
 */
export function StatusPill({
  status,
  label,
  variant,
  className,
  ...props
}: {
  status: string;
  label?: string;
  variant?: StatusTone;
  className?: string;
} & React.ComponentProps<typeof Badge>) {
  return (
    <Badge
      variant={variant ?? statusVariant(status)}
      className={cn("h-5 px-1.5 text-[11px]", className)}
      {...props}
    >
      {label ?? defaultLabel(status)}
    </Badge>
  );
}
