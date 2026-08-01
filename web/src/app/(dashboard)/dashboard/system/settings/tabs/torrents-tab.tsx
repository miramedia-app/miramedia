"use client";

import * as React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { SecretInput } from "@/components/ui/secret-input";
import { TestButton } from "@/components/ui/test-button";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";

const CLIENTS = ["qbittorrent", "transmission", "sabnzbd"] as const;
type Client = (typeof CLIENTS)[number];

const TRAILING_SLASHES = /\/+$/;
const LEADING_SLASHES = /^\/+/;

function clientLabel(c: Client): string {
  return c === "qbittorrent" ? "qBittorrent" : c === "transmission" ? "Transmission" : "SABnzbd";
}

export function TorrentsTab({
  misc,
  setMiscPath,
  torrents,
  setTorrentsPath,
}: {
  misc: AnyObj;
  setMiscPath: SetPath;
  torrents: AnyObj;
  setTorrentsPath: SetPath;
}) {
  const m = misc;
  const tor = torrents;
  const native = (tor.native as AnyObj | undefined) ?? {};
  const [allowSelfSigned, setAllowSelfSigned] = React.useState<Partial<Record<Client, boolean>>>(
    {},
  );

  const base = String(m.torrent_directory ?? "");
  const baseTrimmed = base.replace(TRAILING_SLASHES, "");
  const splitSuffix = (stored: string): string => {
    if (!stored) return "";
    if (!baseTrimmed) return stored;
    if (stored === baseTrimmed) return "";
    const prefix = baseTrimmed + "/";
    return stored.startsWith(prefix) ? stored.slice(prefix.length) : stored;
  };
  const joinSuffix = (suffix: string): string => {
    const cleaned = suffix.replace(LEADING_SLASHES, "").replace(TRAILING_SLASHES, "");
    if (!cleaned) return "";
    if (!baseTrimmed) return cleaned;
    return `${baseTrimmed}/${cleaned}`;
  };
  const completedStored = String(m.completed_torrent_path ?? "");
  const incompleteStored = String(m.incomplete_torrent_path ?? "");
  const completedOutOfBase =
    !!completedStored &&
    !!baseTrimmed &&
    completedStored !== baseTrimmed &&
    !completedStored.startsWith(baseTrimmed + "/");
  const incompleteOutOfBase =
    !!incompleteStored &&
    !!baseTrimmed &&
    incompleteStored !== baseTrimmed &&
    !incompleteStored.startsWith(baseTrimmed + "/");
  const basePrefix = baseTrimmed ? `${baseTrimmed}/` : "";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Torrent Settings</CardTitle>
          <CardDescription>
            Storage, auto-download, and post-import behavior shared across all clients.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>
              Completed Downloads
              <OverrideMarker path={["misc", "completed_torrent_path"]} />
            </Label>
            <div className="flex items-stretch overflow-hidden rounded-md border">
              <span className="flex max-w-[60%] items-center overflow-hidden bg-muted px-3 text-sm text-ellipsis whitespace-nowrap text-muted-foreground">
                {basePrefix || "(set Torrents Directory)"}
              </span>
              <Input
                value={splitSuffix(completedStored)}
                onChange={(e) =>
                  setMiscPath(["completed_torrent_path"], joinSuffix(e.target.value))
                }
                placeholder="(empty = base only)"
                className="flex-1 rounded-none border-0 focus-visible:ring-0 focus-visible:ring-offset-0"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Where finished downloads live and MiraMedia imports from. Must match the save path
              your external client (qBit/Transmission/SAB) writes finished torrents to. Base path is
              the Torrents Directory configured on the General tab.
              {completedOutOfBase && (
                <>
                  {" "}
                  <span className="text-amber-600">
                    Current value is outside the base; clear it to reset.
                  </span>
                </>
              )}
            </p>
          </div>
          <div className="space-y-2">
            <Label>
              Incomplete Downloads (optional)
              <OverrideMarker path={["misc", "incomplete_torrent_path"]} />
            </Label>
            <div className="flex items-stretch overflow-hidden rounded-md border">
              <span className="flex max-w-[60%] items-center overflow-hidden bg-muted px-3 text-sm text-ellipsis whitespace-nowrap text-muted-foreground">
                {basePrefix || "(set Torrents Directory)"}
              </span>
              <Input
                value={splitSuffix(incompleteStored)}
                onChange={(e) =>
                  setMiscPath(["incomplete_torrent_path"], joinSuffix(e.target.value))
                }
                placeholder="incomplete (empty = no split)"
                className="flex-1 rounded-none border-0 focus-visible:ring-0 focus-visible:ring-offset-0"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              In-progress downloads land here. Used by the native client; external clients have
              their own incomplete folder setting. Leave empty to skip the split.
              {incompleteOutOfBase && (
                <>
                  {" "}
                  <span className="text-amber-600">
                    Current value is outside the base; clear it to reset.
                  </span>
                </>
              )}
            </p>
          </div>
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label>
                Cleanup after import
                <OverrideMarker path={["misc", "cleanup_after_import"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Remove torrents from the client after successful hardlink/import.
              </p>
            </div>
            <Switch
              checked={Boolean(m.cleanup_after_import)}
              onCheckedChange={(v) => setMiscPath(["cleanup_after_import"], v)}
            />
          </div>
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label>
                Continuous download
                <OverrideMarker path={["misc", "continuous_download"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Auto-fetch missing episodes/movies on a schedule.
              </p>
            </div>
            <Switch
              checked={Boolean(m.continuous_download)}
              onCheckedChange={(v) => setMiscPath(["continuous_download"], v)}
            />
          </div>
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label>
                Auto-download specials
                <OverrideMarker path={["misc", "download_specials"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Search and download Season 0 specials.
              </p>
            </div>
            <Switch
              checked={Boolean(m.download_specials)}
              onCheckedChange={(v) => setMiscPath(["download_specials"], v)}
            />
          </div>
          <div className="space-y-2">
            <Label>
              Auto-download interval (hours)
              <OverrideMarker path={["misc", "auto_download_interval_hours"]} />
            </Label>
            <Input
              type="number"
              min={1}
              value={Number(m.auto_download_interval_hours ?? "") || ""}
              onChange={(e) =>
                setMiscPath(["auto_download_interval_hours"], Number(e.target.value) || 0)
              }
              placeholder="6"
              className="max-w-[120px]"
            />
          </div>
          <div className="space-y-2">
            <Label>
              Background import sweep (minutes)
              <OverrideMarker path={["misc", "import_sweep_interval_minutes"]} />
            </Label>
            <Input
              type="number"
              min={1}
              value={Number(m.import_sweep_interval_minutes ?? "") || ""}
              onChange={(e) =>
                setMiscPath(["import_sweep_interval_minutes"], Number(e.target.value) || 0)
              }
              placeholder="5"
              className="max-w-[120px]"
            />
            <p className="text-xs text-muted-foreground">
              How often finished torrents are re-checked for import in the background. Manual import
              is unchanged. Reschedules without a restart.
            </p>
          </div>
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label>
                File integrity audit
                <OverrideMarker path={["misc", "integrity_check_enabled"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Store SHA1 hashes on import and periodically verify files on disk.
              </p>
            </div>
            <Switch
              checked={Boolean(m.integrity_check_enabled)}
              onCheckedChange={(v) => setMiscPath(["integrity_check_enabled"], v)}
            />
          </div>
          {Boolean(m.integrity_check_enabled) && (
            <div className="space-y-2">
              <Label>
                Integrity audit interval (hours)
                <OverrideMarker path={["misc", "integrity_check_interval_hours"]} />
              </Label>
              <Input
                type="number"
                min={1}
                value={Number(m.integrity_check_interval_hours ?? "") || ""}
                onChange={(e) =>
                  setMiscPath(["integrity_check_interval_hours"], Number(e.target.value) || 0)
                }
                placeholder="168"
                className="max-w-[120px]"
              />
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Native Torrents</CardTitle>
            <Switch
              checked={Boolean(native.enabled)}
              onCheckedChange={(v) => setTorrentsPath(["native", "enabled"], v)}
            />
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>Max Download Rate (KB/s, 0 = unlimited)</Label>
              <Input
                type="number"
                value={Number(native.max_download_rate ?? "") || ""}
                onChange={(e) =>
                  setTorrentsPath(["native", "max_download_rate"], Number(e.target.value) || 0)
                }
              />
            </div>
            <div className="space-y-2">
              <Label>Max Upload Rate (KB/s, 0 = unlimited)</Label>
              <Input
                type="number"
                value={Number(native.max_upload_rate ?? "") || ""}
                onChange={(e) =>
                  setTorrentsPath(["native", "max_upload_rate"], Number(e.target.value) || 0)
                }
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {CLIENTS.map((client) => {
        const cfg = (tor[client] as AnyObj | undefined) ?? {};
        const isQbit = client === "qbittorrent";
        const isTrans = client === "transmission";
        const isSab = client === "sabnzbd";
        const httpsOn = isTrans
          ? Boolean(cfg.https_enabled)
          : isQbit || isSab
            ? Boolean(cfg.https)
            : false;
        const testConfig = {
          ...cfg,
          ...(allowSelfSigned[client] ? { allow_self_signed: true } : {}),
        };
        return (
          <Card key={client}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>{clientLabel(client)}</CardTitle>
                <div className="flex items-center gap-2">
                  <TestButton integration={client} getConfig={() => testConfig} />
                  <Switch
                    checked={Boolean(cfg.enabled)}
                    onCheckedChange={(v) => setTorrentsPath([client, "enabled"], v)}
                  />
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Host</Label>
                  <Input
                    value={String(cfg.host ?? "")}
                    onChange={(e) => setTorrentsPath([client, "host"], e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Port</Label>
                  <Input
                    type="number"
                    value={Number(cfg.port ?? "") || ""}
                    onChange={(e) => setTorrentsPath([client, "port"], Number(e.target.value) || 0)}
                  />
                </div>
                {!isSab && (
                  <>
                    <div className="space-y-2">
                      <Label>Username</Label>
                      <Input
                        value={String(cfg.username ?? "")}
                        onChange={(e) => setTorrentsPath([client, "username"], e.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Password</Label>
                      <SecretInput
                        value={String(cfg.password ?? "")}
                        onValueChange={(v) => setTorrentsPath([client, "password"], v)}
                      />
                    </div>
                  </>
                )}
                {isQbit && (
                  <>
                    <div className="space-y-2">
                      <Label>Category Name</Label>
                      <Input
                        value={String(cfg.category_name ?? "")}
                        onChange={(e) => setTorrentsPath([client, "category_name"], e.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Category Save Path</Label>
                      <Input
                        value={String(cfg.category_save_path ?? "")}
                        onChange={(e) =>
                          setTorrentsPath([client, "category_save_path"], e.target.value)
                        }
                      />
                    </div>
                  </>
                )}
                {isTrans && (
                  <div className="space-y-2">
                    <Label>RPC Path</Label>
                    <Input
                      value={String(cfg.path ?? "")}
                      onChange={(e) => setTorrentsPath([client, "path"], e.target.value)}
                    />
                  </div>
                )}
                {isSab && (
                  <>
                    <div className="space-y-2">
                      <Label>API Key</Label>
                      <SecretInput
                        value={String(cfg.api_key ?? "")}
                        onValueChange={(v) => setTorrentsPath([client, "api_key"], v)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Base Path</Label>
                      <Input
                        value={String(cfg.base_path ?? "")}
                        onChange={(e) => setTorrentsPath([client, "base_path"], e.target.value)}
                      />
                    </div>
                  </>
                )}
              </div>
              {isTrans && (
                <div className="flex items-center gap-2">
                  <Switch
                    checked={Boolean(cfg.https_enabled)}
                    onCheckedChange={(v) => setTorrentsPath([client, "https_enabled"], v)}
                  />
                  <Label>HTTPS</Label>
                </div>
              )}
              {httpsOn && (
                <div className="flex items-center gap-2">
                  <Checkbox
                    id={`${client}-allow-self-signed`}
                    checked={Boolean(allowSelfSigned[client])}
                    onCheckedChange={(v) =>
                      setAllowSelfSigned((prev) => ({
                        ...prev,
                        [client]: v === true,
                      }))
                    }
                  />
                  <Label htmlFor={`${client}-allow-self-signed`}>
                    Allow self-signed certificate
                  </Label>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
