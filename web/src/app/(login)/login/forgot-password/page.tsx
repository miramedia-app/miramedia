"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import apiClient from "@/lib/api/client";

export default function ForgotPasswordPage() {
  const [email, setEmail] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);
  const [isSuccess, setIsSuccess] = React.useState(false);

  async function requestPasswordReset() {
    if (!email) {
      toast.error("Please enter your email address.");
      return;
    }
    setIsLoading(true);
    const { error } = await apiClient.POST("/api/v1/auth/forgot-password", {
      body: { email },
    });
    if (error) {
      toast.error("Failed to send reset email");
    } else {
      setIsSuccess(true);
      toast.success("Password reset email sent! Check your inbox for instructions.");
    }
    setIsLoading(false);
  }

  return (
    <Card className="mx-auto max-w-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Forgot Password</CardTitle>
        <CardDescription>
          {isSuccess
            ? "We've sent a password reset link to your email address if a SMTP server is configured. Check your inbox and follow the instructions to reset your password. If you didn't receive an email, please contact an administrator; the reset link will be in the logs of MiraMedia."
            : "Enter your email address and we'll send you a link to reset your password."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isSuccess ? (
          <div className="space-y-4">
            <div className="rounded-lg bg-green-50 p-4 text-center dark:bg-green-950">
              <p className="text-sm text-green-700 dark:text-green-300">
                Password reset email sent successfully!
              </p>
            </div>
            <div className="text-center text-sm text-muted-foreground">
              <p>Didn&apos;t receive the email? Check your spam folder or</p>
              <button
                type="button"
                className="text-primary hover:underline"
                onClick={() => {
                  setIsSuccess(false);
                  setEmail("");
                }}
              >
                try again
              </button>
            </div>
          </div>
        ) : (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              requestPasswordReset();
            }}
          >
            <div className="grid gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                placeholder="m@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isLoading}
                required
              />
            </div>
            <Button type="submit" className="w-full" disabled={isLoading || !email}>
              {isLoading ? "Sending Reset Email..." : "Send Reset Email"}
            </Button>
          </form>
        )}
        <div className="mt-4 text-center text-sm">
          <Link className="font-semibold text-primary hover:underline" href="/login">
            Back to Login
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
