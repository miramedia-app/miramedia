"use client";

import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { LoginCard } from "@/components/auth/login-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export default function LoginPage() {
  const metadataQuery = useQuery({
    queryKey: ["auth", "metadata"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/auth/metadata", { signal });
      if (error) throw error;
      return data;
    },
  });

  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-4">
      {metadataQuery.isError && (
        <Alert variant="destructive">
          <AlertTitle>Couldn&apos;t load sign-in options</AlertTitle>
          <AlertDescription className="flex items-center gap-2">
            Single sign-on and registration may be hidden.
            <Button variant="outline" size="sm" onClick={() => metadataQuery.refetch()}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <LoginCard
        oauthProviderNames={metadataQuery.data?.oauth_providers ?? []}
        allowRegistration={metadataQuery.data?.allow_registration ?? false}
      />
    </div>
  );
}
