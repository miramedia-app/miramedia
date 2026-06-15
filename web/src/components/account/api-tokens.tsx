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

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export function ApiTokens() {
  const qc = useQueryClient();
  const tokensQuery = useQuery({
    queryKey: ["users", "me", "tokens"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/users/me/tokens");
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

  const [revealedToken, setRevealedToken] = React.useState<string | null>(null);
  const [revealedName, setRevealedName] = React.useState("");

  const [revoking, setRevoking] = React.useState<Record<string, boolean>>({});

  async function create() {
    if (!newName.trim()) {
      toast.error("Name is required");
      return;
    }
    setCreating(true);
    try {
      const body: { name: string; expires_at?: string | null } = { name: newName.trim() };
      if (newExpiresAt) body.expires_at = new Date(newExpiresAt).toISOString();
      const { data, error } = await apiClient.POST("/api/v1/users/me/tokens", { body });
      if (error || !data) {
        toast.error("Failed to create token");
        return;
      }
      setRevealedToken((data as { token: string }).token);
      setRevealedName((data as { name: string }).name);
      setCreateOpen(false);
      setNewName("");
      setNewExpiresAt("");
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
            </CardDescription>
          </div>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1 h-4 w-4" />
            New Token
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
                  className="flex items-center justify-between gap-3 rounded-lg border p-4"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
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
                    <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted-foreground">
                      <span>Created {formatDate(token.created_at)}</span>
                      <span>Last used {formatDate(token.last_used_at)}</span>
                      {token.expires_at && <span>Expires {formatDate(token.expires_at)}</span>}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
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
              Give the token a memorable name. The plaintext value is shown once after creation.
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
            <div className="flex items-center gap-2">
              <Input value={revealedToken ?? ""} readOnly className="font-mono text-xs" />
              <Button
                variant="outline"
                size="icon"
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
