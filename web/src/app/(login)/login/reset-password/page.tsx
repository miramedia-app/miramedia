"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import apiClient from "@/lib/api/client";
import { submitPasswordReset } from "./submit";

function ResetPasswordInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const resetToken = searchParams.get("token");

  const [newPassword, setNewPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);
  const invalidNotified = React.useRef(false);

  React.useEffect(() => {
    if (!resetToken && !invalidNotified.current) {
      invalidNotified.current = true;
      toast.error("Invalid or missing reset token.");
      router.push("/login");
    }
  }, [resetToken, router]);

  async function resetPassword() {
    if (newPassword !== confirmPassword) {
      toast.error("Passwords do not match.");
      return;
    }
    if (!resetToken) {
      toast.error("Invalid or missing reset token.");
      return;
    }
    await submitPasswordReset({
      request: () =>
        apiClient.POST("/api/v1/auth/reset-password", {
          body: { password: newPassword, token: resetToken },
        }),
      setLoading: setIsLoading,
      onSuccess: () => {
        toast.success("Password reset successfully! You can now log in with your new password.");
        router.push("/login");
      },
      onHttpFailure: () => {
        toast.error("Failed to reset password");
      },
      onTransportError: () => {
        toast.error("Unable to reach server. Please try again.");
      },
    });
  }

  return (
    <Card className="mx-auto max-w-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Reset Password</CardTitle>
        <CardDescription>Enter your new password below.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            resetPassword();
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="new-password">New Password</Label>
            <Input
              id="new-password"
              type="password"
              minLength={1}
              placeholder="Enter your new password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={isLoading}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="confirm-password">Confirm Password</Label>
            <Input
              id="confirm-password"
              type="password"
              minLength={1}
              placeholder="Confirm your new password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={isLoading}
              required
            />
          </div>
          <Button
            className="w-full"
            type="submit"
            disabled={isLoading || !newPassword || !confirmPassword}
          >
            {isLoading ? "Resetting Password..." : "Reset Password"}
          </Button>
        </form>
        <div className="mt-4 text-center text-sm">
          <Link className="font-semibold text-primary hover:underline" href="/login">
            Back to Login
          </Link>
          <span className="mx-2 text-muted-foreground">•</span>
          <Link className="text-primary hover:underline" href="/login/forgot-password">
            Request New Reset Link
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <React.Suspense fallback={null}>
      <ResetPasswordInner />
    </React.Suspense>
  );
}
