"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { SecretInput } from "@/components/ui/secret-input";
import { NullableInput } from "@/components/ui/nullable-input";
import { TestButton } from "@/components/ui/test-button";
import { OverrideMarker } from "../_marker";
import { csvToArray, type AnyObj, type SetPath } from "../_shared";

export function NotificationsTab({
  notifications,
  setNotificationsPath,
}: {
  notifications: AnyObj;
  setNotificationsPath: SetPath;
}) {
  const not = notifications;
  const native = (not.native as AnyObj | undefined) ?? {};
  const email = (not.email_notifications as AnyObj | undefined) ?? {};
  const smtp = (not.smtp_config as AnyObj | undefined) ?? {};
  const gotify = (not.gotify as AnyObj | undefined) ?? {};
  const ntfy = (not.ntfy as AnyObj | undefined) ?? {};
  const pushover = (not.pushover as AnyObj | undefined) ?? {};
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Notification Settings</CardTitle>
          <CardDescription>Shared settings applied to all notification providers.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <Label>
              Subject Prefix
              <OverrideMarker path={["notifications", "subject_prefix"]} />
            </Label>
            <Input
              value={String(not.subject_prefix ?? "")}
              onChange={(e) => setNotificationsPath(["subject_prefix"], e.target.value)}
              placeholder="[MiraMedia]"
            />
            <p className="text-xs text-muted-foreground">
              Optional. Prepended to every external notification title. Empty = no prefix.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Native Notifications</CardTitle>
              <CardDescription>
                In-app notifications page and bell icon. Disabling hides the page and stops storing
                internal notification records.
              </CardDescription>
            </div>
            <Switch
              checked={Boolean(native.enabled ?? true)}
              onCheckedChange={(v) => setNotificationsPath(["native", "enabled"], v)}
            />
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <Label>
              Retention (days)
              <OverrideMarker path={["notifications", "native", "retention_days"]} />
            </Label>
            <Input
              type="number"
              min={1}
              value={Number(native.retention_days ?? "") || ""}
              onChange={(e) =>
                setNotificationsPath(["native", "retention_days"], Number(e.target.value) || 0)
              }
              placeholder="30"
              className="max-w-[120px]"
            />
            <p className="text-xs text-muted-foreground">
              Read notifications older than this are auto-deleted daily. Unread notifications are
              never deleted.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Email</CardTitle>
            <div className="flex items-center gap-2">
              <TestButton integration="smtp" getConfig={() => smtp} />
              <Switch
                checked={Boolean(email.enabled)}
                onCheckedChange={(v) => setNotificationsPath(["email_notifications", "enabled"], v)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>SMTP Host</Label>
              <Input
                value={String(smtp.smtp_host ?? "")}
                onChange={(e) => setNotificationsPath(["smtp_config", "smtp_host"], e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>SMTP Port</Label>
              <Input
                type="number"
                value={Number(smtp.smtp_port ?? "") || ""}
                onChange={(e) =>
                  setNotificationsPath(["smtp_config", "smtp_port"], Number(e.target.value) || 0)
                }
              />
            </div>
            <div className="space-y-2">
              <Label>SMTP User</Label>
              <Input
                value={String(smtp.smtp_user ?? "")}
                onChange={(e) => setNotificationsPath(["smtp_config", "smtp_user"], e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>SMTP Password</Label>
              <SecretInput
                value={String(smtp.smtp_password ?? "")}
                onValueChange={(v) => setNotificationsPath(["smtp_config", "smtp_password"], v)}
              />
            </div>
            <div className="space-y-2">
              <Label>From Email</Label>
              <Input
                value={String(smtp.from_email ?? "")}
                onChange={(e) =>
                  setNotificationsPath(["smtp_config", "from_email"], e.target.value)
                }
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              checked={Boolean(smtp.use_tls)}
              onCheckedChange={(v) => setNotificationsPath(["smtp_config", "use_tls"], v)}
            />
            <Label>Use TLS</Label>
          </div>
          <Separator />
          <div className="space-y-2">
            <Label>Recipient Emails (comma-separated)</Label>
            <Input
              value={Array.isArray(email.emails) ? (email.emails as string[]).join(", ") : ""}
              onChange={(e) =>
                setNotificationsPath(["email_notifications", "emails"], csvToArray(e.target.value))
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Gotify</CardTitle>
            <div className="flex items-center gap-2">
              <TestButton integration="gotify" getConfig={() => gotify} />
              <Switch
                checked={Boolean(gotify.enabled)}
                onCheckedChange={(v) => setNotificationsPath(["gotify", "enabled"], v)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>URL</Label>
              <NullableInput
                value={gotify.url as string | null}
                onValueChange={(v) => setNotificationsPath(["gotify", "url"], v)}
                type="url"
                placeholder="https://gotify.example.com"
              />
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <SecretInput
                value={String(gotify.api_key ?? "")}
                onValueChange={(v) => setNotificationsPath(["gotify", "api_key"], v)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>ntfy</CardTitle>
            <div className="flex items-center gap-2">
              <TestButton integration="ntfy" getConfig={() => ntfy} />
              <Switch
                checked={Boolean(ntfy.enabled)}
                onCheckedChange={(v) => setNotificationsPath(["ntfy", "enabled"], v)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>URL</Label>
            <NullableInput
              value={ntfy.url as string | null}
              onValueChange={(v) => setNotificationsPath(["ntfy", "url"], v)}
              type="url"
              placeholder="https://ntfy.sh/your-topic"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Pushover</CardTitle>
            <div className="flex items-center gap-2">
              <TestButton integration="pushover" getConfig={() => pushover} />
              <Switch
                checked={Boolean(pushover.enabled)}
                onCheckedChange={(v) => setNotificationsPath(["pushover", "enabled"], v)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>API Key</Label>
              <SecretInput
                value={String(pushover.api_key ?? "")}
                onValueChange={(v) => setNotificationsPath(["pushover", "api_key"], v)}
              />
            </div>
            <div className="space-y-2">
              <Label>User Key</Label>
              <SecretInput
                value={String(pushover.user ?? "")}
                onValueChange={(v) => setNotificationsPath(["pushover", "user"], v)}
              />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
