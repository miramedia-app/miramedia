"use client";

import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { SignupCard } from "@/components/auth/signup-card";

export default function SignupPage() {
  const { data } = useQuery({
    queryKey: ["auth", "metadata"],
    queryFn: async ({ signal }) => {
      const { data } = await apiClient.GET("/api/v1/auth/metadata", { signal });
      return data ?? { oauth_providers: [] };
    },
  });
  return <SignupCard oauthProviderNames={data?.oauth_providers ?? []} />;
}
