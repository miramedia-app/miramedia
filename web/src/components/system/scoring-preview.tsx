"use client";

import * as React from "react";
import { toast } from "sonner";
import { LoaderCircle, FlaskConical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type Result = components["schemas"]["ScoringPreviewResponse"];
type BreakdownEntry = components["schemas"]["ScoringPreviewBreakdownEntry"];

export function ScoringPreview() {
  const [title, setTitle] = React.useState("The.Show.S01E01.1080p.WEB-DL.x265-GROUP");
  const [flagsRaw, setFlagsRaw] = React.useState("");
  const [seeders, setSeeders] = React.useState(20);
  const [ageDays, setAgeDays] = React.useState(3);

  const [running, setRunning] = React.useState(false);
  const [result, setResult] = React.useState<Result | null>(null);

  const matched: BreakdownEntry[] = result?.breakdown.filter((e) => e.matched) ?? [];
  const unmatched: BreakdownEntry[] = result?.breakdown.filter((e) => !e.matched) ?? [];

  async function run() {
    setRunning(true);
    try {
      const flags = flagsRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const { data, error } = await apiClient.POST("/api/v1/indexers/scoring/preview", {
        body: { title, flags, seeders, age_days: ageDays },
      });
      if (error || !data) {
        toast.error("Preview failed");
        return;
      }
      setResult(data);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-3 flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-muted-foreground" />
        <h4 className="text-sm font-medium">Score Preview</h4>
        <span className="text-xs text-muted-foreground">
          Try a synthetic torrent against your current rules.
        </span>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-2 md:col-span-2">
          <Label htmlFor="preview-title">Torrent title</Label>
          <Input id="preview-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="preview-flags">Indexer flags (comma-separated)</Label>
          <Input
            id="preview-flags"
            value={flagsRaw}
            onChange={(e) => setFlagsRaw(e.target.value)}
            placeholder="freeleech, internal"
          />
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="preview-seeders">Seeders</Label>
            <Input
              id="preview-seeders"
              type="number"
              min={0}
              value={seeders}
              onChange={(e) => setSeeders(parseInt(e.target.value, 10) || 0)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="preview-age">Age (days)</Label>
            <Input
              id="preview-age"
              type="number"
              min={0}
              value={ageDays}
              onChange={(e) => setAgeDays(parseInt(e.target.value, 10) || 0)}
            />
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Button size="sm" onClick={() => void run()} disabled={running}>
          {running ? (
            <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <FlaskConical className="mr-1 h-4 w-4" />
          )}
          Preview score
        </Button>
        {result && (
          <span className="text-sm">
            Total: <span className="font-mono font-medium">{result.total}</span>
          </span>
        )}
      </div>

      {result ? (
        <div className="mt-4 space-y-3">
          {matched.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground uppercase">
                Matched ({matched.length})
              </p>
              <table className="w-full text-xs">
                <tbody>
                  {matched.map((entry) => (
                    <tr key={entry.rule} className="border-b last:border-0">
                      <td className="py-1 pr-2 font-mono">{entry.rule}</td>
                      <td
                        className={`py-1 pr-2 text-right font-mono ${
                          entry.delta < 0 ? "text-red-500" : "text-green-600"
                        }`}
                      >
                        {entry.delta > 0 ? "+" : ""}
                        {entry.delta}
                      </td>
                      <td className="py-1 text-muted-foreground">{entry.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {unmatched.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">
                Unmatched ({unmatched.length})
              </summary>
              <table className="mt-2 w-full">
                <tbody>
                  {unmatched.map((entry) => (
                    <tr key={entry.rule} className="border-b last:border-0">
                      <td className="py-1 pr-2 font-mono">{entry.rule}</td>
                      <td className="py-1 text-muted-foreground">{entry.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          Click{" "}
          <Badge variant="outline" className="text-xs">
            Preview score
          </Badge>{" "}
          to see how each rule would score this title.
        </p>
      )}
    </div>
  );
}
