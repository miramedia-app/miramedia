"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";
import { useQueryClient } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { beginAuthTransition, handleOauth, hardNavigate } from "@/lib/auth";

type Props = {
  oauthProviderNames: string[];
};

export function LoginCard({ oauthProviderNames }: Props) {
  const router = useRouter();
  const qc = useQueryClient();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [errorMessage, setErrorMessage] = React.useState("");
  const [successMessage, setSuccessMessage] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);

  async function handleLogin(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setErrorMessage("");
    setSuccessMessage("");

    try {
      // Transition BEFORE authenticating, not just before navigating: this
      // advances the auth generation and drains any in-flight 401 exit, so a
      // response still travelling for the previous session cannot land on — or
      // log out — the session this POST is about to establish.
      await beginAuthTransition(qc);

      const { error } = await apiClient.POST("/api/v1/auth/cookie/login", {
        body: {
          username: email,
          password,
          scope: "",
        },
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });

      if (!error) {
        const message = "Login successful! Redirecting...";
        setSuccessMessage(message);
        toast.success(message);
        // Full document load: no QueryObserver from the previous session can
        // survive into the new one.
        hardNavigate("/dashboard", (p) => router.push(p));
      } else {
        toast.error("Login failed!");
        setErrorMessage("Login failed! Please check your credentials and try again.");
      }
    } catch (err) {
      console.error("Login request threw:", err);
      const detail = err instanceof Error ? err.message : String(err);
      toast.error("Unable to reach server.");
      setErrorMessage(`Unable to reach server: ${detail}`);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <>
      <Card className="mx-auto max-w-sm">
        <CardHeader>
          <CardTitle className="text-2xl">Login</CardTitle>
          <CardDescription>Enter your email below to log in to your account</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4" onSubmit={handleLogin}>
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
              <div className="flex items-center">
                <Label htmlFor="password">Password</Label>
                <Link
                  className="ml-auto inline-block text-sm underline"
                  href="/login/forgot-password"
                  tabIndex={-1}
                >
                  Forgot your password?
                </Link>
              </div>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
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

            <Button className="w-full" disabled={isLoading} type="submit">
              {isLoading && <Spinner className="mr-2" />}
              Login
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
                onClick={() => void handleOauth(qc, (msg) => toast.error(msg))}
              >
                Login with {name}
              </Button>
            </React.Fragment>
          ))}
          <div className="mt-4 text-center text-sm">
            <Button render={<Link href="/login/signup" />} variant="link">
              Don&apos;t have an account? Sign up
            </Button>
          </div>
        </CardContent>
      </Card>

      {successMessage && (
        <div className="relative">
          <div className="absolute right-0 left-0 mx-auto mt-4 max-w-sm space-y-2">
            <Alert variant="default">
              <AlertTitle>Success</AlertTitle>
              <AlertDescription>{successMessage}</AlertDescription>
            </Alert>
          </div>
        </div>
      )}
    </>
  );
}
