"use client";

import * as React from "react";
import { FlaskConical, LoaderCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import apiClient from "@/lib/api/client";

type Integration =
  | "smtp"
  | "qbittorrent"
  | "transmission"
  | "sabnzbd"
  | "tmdb"
  | "tvdb"
  | "bazarr"
  | "gotify"
  | "ntfy"
  | "pushover"
  | "seerr"
  | "oidc";

type Props = {
  integration: Integration;
  getConfig: () => Record<string, unknown>;
  disabled?: boolean;
  label?: string;
  size?: "sm" | "default";
};

export function TestButton({
  integration,
  getConfig,
  disabled = false,
  label = "Test connection",
  size = "sm",
}: Props) {
  const [testing, setTesting] = React.useState(false);

  async function runTest() {
    if (testing) return;
    setTesting(true);
    try {
      const { data, error, response } = await apiClient.POST(
        "/api/v1/system/settings/integrations/{integration}/test",
        {
          params: { path: { integration } },
          body: { config: getConfig() },
        },
      );
      if (error) {
        if (response.status === 429) {
          toast.error("Rate limited", {
            description: "Too many test requests; try again in a minute.",
          });
        } else {
          toast.error("Test failed", { description: "See server logs." });
        }
        return;
      }
      const latency = data?.latency_ms != null ? ` (${data.latency_ms}ms)` : "";
      if (data?.ok) {
        toast.success(`${integration}${latency}`, { description: data.message });
      } else {
        toast.error(`${integration} failed${latency}`, { description: data?.message });
      }
    } catch (e) {
      toast.error("Test failed", { description: String(e) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <Button
      type="button"
      variant="outline"
      size={size}
      disabled={disabled || testing}
      onClick={runTest}
    >
      {testing ? (
        <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
      ) : (
        <FlaskConical className="mr-1 h-4 w-4" />
      )}
      {label}
    </Button>
  );
}
