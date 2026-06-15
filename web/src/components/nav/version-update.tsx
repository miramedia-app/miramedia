"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import apiClient from "@/lib/api/client";

const DISMISS_KEY = "mm.update.dismissed";
const LEGACY_DISMISS_PREFIX = "mm.update.dismissed.";
const LEGACY_CLEANUP_FLAG = "mm.update.cleanup_v1";

function readDismiss(target: string | null): boolean {
  if (!target || typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(DISMISS_KEY) === target;
  } catch {
    return false;
  }
}

function writeDismiss(target: string | null) {
  if (!target || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(DISMISS_KEY, target);
    // Scan legacy per-version keys at most once per browser via a flag.
    if (window.localStorage.getItem(LEGACY_CLEANUP_FLAG) !== "1") {
      for (let i = window.localStorage.length - 1; i >= 0; i--) {
        const key = window.localStorage.key(i);
        if (key && key.startsWith(LEGACY_DISMISS_PREFIX)) {
          window.localStorage.removeItem(key);
        }
      }
      window.localStorage.setItem(LEGACY_CLEANUP_FLAG, "1");
    }
  } catch {
    // ignore
  }
}

export function VersionUpdate() {
  const { data } = useQuery({
    queryKey: ["system", "updates"],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/system/updates");
      return data ?? null;
    },
    staleTime: 60 * 60 * 1000,
  });

  const [dismissed, setDismissed] = React.useState(false);
  const latestVersion = data?.latest_version ?? null;

  React.useEffect(() => {
    setDismissed(readDismiss(latestVersion));
  }, [latestVersion]);

  if (!data?.enabled) return null;
  const updateAvailable = !!data.update_available;
  if (!updateAvailable || dismissed || !latestVersion) return null;

  return (
    <div className="mx-2 mb-2 rounded-lg bg-muted/50 p-4">
      <p className="text-sm font-bold">New Version</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {data.current_version ?? "—"} → {latestVersion}
      </p>
      <Button
        render={<Link href="/dashboard/system/settings?tab=updates" />}
        className="mt-3 w-full"
      >
        View update
      </Button>
      <button
        type="button"
        className="mt-2 w-full text-xs text-muted-foreground underline hover:text-foreground"
        onClick={() => {
          writeDismiss(latestVersion);
          setDismissed(true);
        }}
      >
        Dismiss
      </button>
    </div>
  );
}
