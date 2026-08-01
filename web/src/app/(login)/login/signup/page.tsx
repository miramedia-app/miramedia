"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { SignupCard } from "@/components/auth/signup-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";

function RegistrationUnavailableCard({ description }: { description: string }) {
  return (
    <Card className="mx-auto max-w-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Sign up</CardTitle>
        <CardDescription>Registration is disabled on this server.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-sm text-muted-foreground">{description}</p>
        <Button render={<Link href="/login" />} variant="outline" className="w-full">
          Back to login
        </Button>
      </CardContent>
    </Card>
  );
}

export default function SignupPage() {
  const { data, isPending } = useQuery({
    queryKey: ["auth", "metadata"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/auth/metadata", { signal });
      if (error || !data) {
        return { oauth_providers: [], allow_registration: false };
      }
      return data;
    },
  });

  if (data?.allow_registration === true) {
    return <SignupCard oauthProviderNames={data.oauth_providers} />;
  }

  if (isPending) {
    return (
      <Card className="mx-auto max-w-sm">
        <CardHeader>
          <CardTitle className="text-2xl">Sign up</CardTitle>
          <CardDescription>Checking whether registration is available…</CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center py-6">
          <Spinner />
        </CardContent>
      </Card>
    );
  }

  return <RegistrationUnavailableCard description="Ask an administrator for an invite." />;
}
