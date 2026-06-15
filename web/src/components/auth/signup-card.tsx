"use client";

import * as React from "react";
import Link from "next/link";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { LoadingBar } from "@/components/loading-bar";
import apiClient from "@/lib/api/client";
import { handleOauth } from "@/lib/auth";

export function SignupCard({ oauthProviderNames }: { oauthProviderNames: string[] }) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [errorMessage, setErrorMessage] = React.useState("");
  const [successMessage, setSuccessMessage] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);

  async function handleSignup(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setErrorMessage("");
    setSuccessMessage("");
    try {
      const { response } = await apiClient.POST("/api/v1/auth/register", {
        body: {
          email,
          password,
          is_active: null,
          is_superuser: null,
          is_verified: null,
        },
      });
      if (response.ok) {
        const msg = "Registration successful! Please login.";
        setSuccessMessage(msg);
        toast.success(msg);
      } else {
        toast.error("Registration failed");
      }
    } catch (err) {
      console.error("Signup request threw:", err);
      const detail = err instanceof Error ? err.message : String(err);
      toast.error("Unable to reach server.");
      setErrorMessage(`Unable to reach server: ${detail}`);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <Card className="mx-auto max-w-sm">
      <CardHeader>
        <CardTitle className="text-xl">Sign Up</CardTitle>
        <CardDescription>Enter your information to create an account</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-4" onSubmit={handleSignup}>
          <div className="grid gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="m@example.com"
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="confirm-password">Confirm Password</Label>
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />
          </div>
          {errorMessage && (
            <Alert variant="destructive">
              <AlertCircle className="size-4" />
              <AlertTitle>Error</AlertTitle>
              <AlertDescription>{errorMessage}</AlertDescription>
            </Alert>
          )}
          {successMessage && (
            <Alert variant="default">
              <CheckCircle2 className="size-4" />
              <AlertTitle>Success</AlertTitle>
              <AlertDescription>{successMessage}</AlertDescription>
            </Alert>
          )}
          {isLoading && <LoadingBar />}
          <Button
            className="w-full"
            disabled={isLoading || password !== confirmPassword || password === ""}
            type="submit"
          >
            Create an account
          </Button>
        </form>
        {oauthProviderNames.map((name) => (
          <React.Fragment key={name}>
            <div className="relative mt-2 text-center text-sm after:absolute after:inset-0 after:top-1/2 after:z-0 after:flex after:items-center after:border-t after:border-border">
              <span className="relative z-10 bg-background px-2 text-muted-foreground">
                Or continue with
              </span>
            </div>
            <Button
              className="mt-2 w-full"
              variant="outline"
              onClick={() => handleOauth((m) => toast.error(m))}
            >
              Login with {name}
            </Button>
          </React.Fragment>
        ))}
        <div className="mt-4 text-center text-sm">
          <Button render={<Link href="/login" />} variant="link">
            Already have an account? Login
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
