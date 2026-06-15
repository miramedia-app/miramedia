"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { SecretInput } from "@/components/ui/secret-input";
import { TestButton } from "@/components/ui/test-button";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";

export function AuthTab({ auth, setAuthPath }: { auth: AnyObj; setAuthPath: SetPath }) {
  const au = auth;
  const oidc = (au.openid_connect as AnyObj | undefined) ?? {};
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Authentication Settings</CardTitle>
          <CardDescription>Session and password reset configuration</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>
                Session Lifetime (seconds)
                <OverrideMarker path={["auth", "session_lifetime"]} />
              </Label>
              <Input
                type="number"
                value={Number(au.session_lifetime ?? "") || ""}
                onChange={(e) => setAuthPath(["session_lifetime"], Number(e.target.value) || 0)}
              />
            </div>
          </div>
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <Label>Email Password Resets</Label>
            <Switch
              checked={Boolean(au.email_password_resets)}
              onCheckedChange={(v) => setAuthPath(["email_password_resets"], v)}
            />
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>OpenID Connect</CardTitle>
          <CardDescription>Single sign-on via OpenID Connect provider</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Switch
              checked={Boolean(oidc.enabled)}
              onCheckedChange={(v) => setAuthPath(["openid_connect", "enabled"], v)}
            />
            <Label>Enabled</Label>
            <div className="ml-auto">
              <TestButton integration="oidc" getConfig={() => oidc} />
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>Provider Name</Label>
              <Input
                value={String(oidc.name ?? "")}
                onChange={(e) => setAuthPath(["openid_connect", "name"], e.target.value)}
                placeholder="OAuth2"
              />
            </div>
            <div className="space-y-2">
              <Label>Client ID</Label>
              <Input
                value={String(oidc.client_id ?? "")}
                onChange={(e) => setAuthPath(["openid_connect", "client_id"], e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>Client Secret</Label>
              <SecretInput
                value={String(oidc.client_secret ?? "")}
                onValueChange={(v) => setAuthPath(["openid_connect", "client_secret"], v)}
              />
            </div>
            <div className="space-y-2">
              <Label>Configuration Endpoint</Label>
              <Input
                value={String(oidc.configuration_endpoint ?? "")}
                onChange={(e) =>
                  setAuthPath(["openid_connect", "configuration_endpoint"], e.target.value)
                }
                placeholder="https://example.com/.well-known/openid-configuration"
              />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
