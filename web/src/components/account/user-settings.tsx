"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Save } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useUser } from "@/components/providers/user-provider";
import apiClient from "@/lib/api/client";

export function UserSettings() {
  const { user } = useUser();
  const qc = useQueryClient();

  const [newPassword, setNewPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [newEmail, setNewEmail] = React.useState("");
  const [isPending, startTransition] = React.useTransition();

  const passwordMismatch = confirmPassword !== "" && newPassword !== confirmPassword;
  const canSave =
    (newEmail !== "" || (newPassword !== "" && newPassword === confirmPassword)) && !isPending;

  function saveUser() {
    if (!canSave) return;
    startTransition(async () => {
      const { error } = await apiClient.PATCH("/api/v1/users/me", {
        body: {
          ...(newPassword !== "" && { password: newPassword }),
          ...(newEmail !== "" && { email: newEmail }),
        },
      });
      if (error) {
        toast.error("Failed to update account");
      } else {
        toast.success("Account updated successfully.");
        setNewPassword("");
        setConfirmPassword("");
        setNewEmail("");
      }
      await qc.invalidateQueries({ queryKey: ["users", "me"] });
    });
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1.5">
          <CardTitle>Account</CardTitle>
          <CardDescription>Update your account email and password</CardDescription>
        </div>
        <Button onClick={saveUser} disabled={!canSave} size="sm">
          <Save className="mr-1.5 h-4 w-4" />
          Save Changes
        </Button>
      </CardHeader>
      <CardContent className="space-y-8">
        <div className="space-y-4 rounded-lg border p-4">
          <div>
            <h3 className="text-sm font-medium">Email</h3>
            <p className="text-sm text-muted-foreground">Update your account email address</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>Current Email</Label>
              <Input value={user?.email ?? ""} disabled />
            </div>
            <div className="space-y-2">
              <Label>New Email</Label>
              <Input
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="Leave empty to keep current email"
                type="email"
              />
            </div>
          </div>
        </div>

        <div className="space-y-4 rounded-lg border p-4">
          <div>
            <h3 className="text-sm font-medium">Password</h3>
            <p className="text-sm text-muted-foreground">Update your account password</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>New Password</Label>
              <Input
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="Enter new password"
                type="password"
              />
            </div>
            <div className="space-y-2">
              <Label>Confirm Password</Label>
              <Input
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm new password"
                type="password"
                className={passwordMismatch ? "border-destructive" : undefined}
              />
              {passwordMismatch && (
                <p className="text-xs text-destructive">Passwords do not match</p>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
