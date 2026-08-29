"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, KeyRound, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { copyToClipboard } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type ApiTokenRead = components["schemas"]["ApiTokenRead"];

const API_TOKEN_SCOPE_OPTIONS: { value: string; label: string }[] = [
  { value: "library:read", label: "Library read — catalog, queue, notifications" },
  { value: "library:write", label: "Library write — add, skip, watchlists, requests" },
  { value: "downloads:write", label: "Downloads write — search and start torrents" },
  { value: "playback:write", label: "Playback write — own progress and watched state" },
  { value: "ops:read", label: "Ops read — health details, logs, updates" },
  { value: "settings:write", label: "Settings write — config and indexer CRUD" },
];

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export function ApiTokens() {
  const qc = useQueryClient();
  const tokensQuery = useQuery({
    queryKey: ["users", "me", "tokens"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/users/me/tokens", { signal });
      if (error) {
        toast.error("Failed to load API tokens");
        return [] as ApiTokenRead[];
      }
      return (data ?? []) as ApiTokenRead[];
    },
  });
  const tokens = tokensQuery.data ?? [];
  const loading = tokensQuery.isLoading;

  const [createOpen, setCreateOpen] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const [newName, setNewName] = React.useState("");
  const [newExpiresAt, setNewExpiresAt] = React.useState("");
  const [selectedScopes, setSelectedScopes] = React.useState<string[]>([]);

  const [revealedToken, setRevealedToken] = React.useState<string | null>(null);
  const [revealedName, setRevealedName] = React.useState("");
  const [revealedScopes, setRevealedScopes] = React.useState<string[]>([]);

  const [revoking, setRevoking] = React.useState<Record<string, boolean>>({});

  function toggleScope(scope: string, checked: boolean) {
    setSelectedScopes((prev) => (checked ? [...prev, scope] : prev.filter((s) => s !== scope)));
  }

  async function create() {
    if (!newName.trim()) {
      toast.error("Name is required");
      return;
    }
    setCreating(true);
    try {
      const body: { name: string; scopes: string[]; expires_at?: string | null } = {
        name: newName.trim(),
        scopes: selectedScopes,
      };
      if (newExpiresAt) body.expires_at = new Date(newExpiresAt).toISOString();
      const { data, error } = await apiClient.POST("/api/v1/users/me/tokens", { body });
      if (error || !data) {
        toast.error("Failed to create token");
        return;
      }
      setRevealedToken((data as { token: string }).token);
      setRevealedName((data as { name: string }).name);
      setRevealedScopes((data as { scopes: string[] }).scopes ?? []);
      setCreateOpen(false);
      setNewName("");
      setNewExpiresAt("");
      setSelectedScopes([]);
      await qc.invalidateQueries({ queryKey: ["users", "me", "tokens"] });
    } finally {
      setCreating(false);
    }
  }

  async function revoke(token: ApiTokenRead) {
    if (!confirm(`Revoke token "${token.name}"? This cannot be undone.`)) return;
    setRevoking((r) => ({ ...r, [token.id]: true }));
    try {
      const { error } = await apiClient.DELETE("/api/v1/users/me/tokens/{token_id}", {
        params: { path: { token_id: token.id } },
      });
      if (error) {
        toast.error("Failed to revoke token");
        return;
      }
      toast.success(`Revoked "${token.name}"`);
      await qc.invalidateQueries({ queryKey: ["users", "me", "tokens"] });
    } finally {
      // Delete the key rather than setting it to false — otherwise the
      // record grows unbounded across the session as tokens come and go.
      setRevoking((r) => {
        const next = { ...r };
        delete next[token.id];
        return next;
      });
    }
  }

  async function copyTokenToClipboard() {
    if (!revealedToken) return;
    try {
      await copyToClipboard(revealedToken);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Clipboard access denied");
    }
  }

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>API Tokens</CardTitle>
            <CardDescription>
              Personal access tokens for headless API use. Send as{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">
                Authorization: Bearer mm_…
              </code>
              . Choose scopes at creation — existing tokens without scopes no longer work after
              upgrade.
            </CardDescription>
          </div>
          <Button size="sm" className="gap-1" onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            Add Token
          </Button>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <LoaderCircle className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : tokens.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">No API tokens yet.</p>
          ) : (
            <ul className="space-y-3">
              {tokens.map((token) => (
                <li
                  key={token.id}
                  className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <KeyRound className="h-4 w-4 text-muted-foreground" />
                      <span className="font-medium">{token.name}</span>
                      <Badge variant="outline" className="font-mono text-xs">
                        …{token.preview}
                      </Badge>
                      {token.expires_at && new Date(token.expires_at) < new Date() && (
                        <Badge variant="destructive" className="text-xs">
                          Expired
                        </Badge>
                      )}
                    </div>
                    {token.scopes?.length ? (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {token.scopes.map((scope) => (
                          <Badge key={scope} variant="secondary" className="font-mono text-xs">
                            {scope}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-1 text-xs text-muted-foreground">
                        No scopes — cannot call API routes
                      </p>
                    )}
                    <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted-foreground">
                      <span>Created {formatDate(token.created_at)}</span>
                      <span>Last used {formatDate(token.last_used_at)}</span>
                      {token.expires_at && <span>Expires {formatDate(token.expires_at)}</span>}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="self-end sm:self-auto"
                    onClick={() => void revoke(token)}
                    disabled={revoking[token.id]}
                    aria-label="Revoke token"
                  >
                    {revoking[token.id] ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4 text-muted-foreground" />
                    )}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create API Token</DialogTitle>
            <DialogDescription>
              Give the token a memorable name and select allowed scopes. The plaintext value is
              shown once after creation.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="token-name">Name</Label>
              <Input
                id="token-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Home Assistant"
                maxLength={120}
              />
            </div>
            <div className="space-y-2">
              <Label>Scopes</Label>
              <ul className="space-y-2 rounded-md border p-3">
                {API_TOKEN_SCOPE_OPTIONS.map((option) => (
                  <li key={option.value} className="flex items-start gap-2">
                    <Checkbox
                      id={`scope-${option.value}`}
                      checked={selectedScopes.includes(option.value)}
                      onCheckedChange={(checked) => toggleScope(option.value, checked === true)}
                    />
                    <label
                      htmlFor={`scope-${option.value}`}
                      className="cursor-pointer text-sm leading-snug"
                    >
                      <span className="font-mono text-xs">{option.value}</span>
                      <span className="block text-muted-foreground">{option.label}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
            <div className="space-y-2">
              <Label htmlFor="token-expires">Expires (optional)</Label>
              <Input
                id="token-expires"
                type="datetime-local"
                value={newExpiresAt}
                onChange={(e) => setNewExpiresAt(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">Leave empty for a non-expiring token.</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={creating}>
              Cancel
            </Button>
            <Button onClick={() => void create()} disabled={creating}>
              {creating && <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={revealedToken !== null}
        onOpenChange={(o) => {
          if (!o) setRevealedToken(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Token Created</DialogTitle>
            <DialogDescription>Save this token now. It will not be shown again.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label>{revealedName}</Label>
            {revealedScopes.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {revealedScopes.map((scope) => (
                  <Badge key={scope} variant="secondary" className="font-mono text-xs">
                    {scope}
                  </Badge>
                ))}
              </div>
            )}
            <div className="flex items-center gap-2">
              <Input value={revealedToken ?? ""} readOnly className="font-mono text-xs" />
              <Button
                variant="outline"
                size="icon"
                className="shrink-0"
                onClick={() => void copyTokenToClipboard()}
                aria-label="Copy"
              >
                <Copy className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => setRevealedToken(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
