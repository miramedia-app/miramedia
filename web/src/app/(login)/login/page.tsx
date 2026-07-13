"use client";

import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { LoginCard } from "@/components/auth/login-card";

export default function LoginPage() {
  const { data } = useQuery({
    queryKey: ["auth", "metadata"],
    queryFn: async ({ signal }) => {
      const { data } = await apiClient.GET("/api/v1/auth/metadata", { signal });
      return data ?? { oauth_providers: [] };
    },
  });

  return (
    <main>
      <LoginCard oauthProviderNames={data?.oauth_providers ?? []} />
    </main>
  );
}
