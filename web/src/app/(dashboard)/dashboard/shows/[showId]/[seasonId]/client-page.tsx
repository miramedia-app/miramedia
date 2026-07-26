"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRouteUuid } from "@/lib/use-route-id";
import { Button } from "@/components/ui/button";
import { DashboardHeader } from "@/components/dashboard-header";

// There is no separate season page — season details live inline on
// the show page. This page exists to satisfy
// the dynamic [seasonId] segment in the static export. On mount it redirects
// to the parent show page where season details are rendered.
export default function SeasonDetailClientPage() {
  const showId = useRouteUuid("showId", 0);
  const router = useRouter();

  React.useEffect(() => {
    if (showId) router.replace(`/dashboard/shows/${showId}`);
  }, [showId, router]);

  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Shows", href: "/dashboard/shows" },
          {
            label: "Show",
            href: showId ? `/dashboard/shows/${showId}` : undefined,
          },
          { label: "Season" },
        ]}
      />
      <main className="flex w-full flex-col items-center justify-center gap-4 p-12 text-center">
        <p className="text-muted-foreground">Redirecting to show page…</p>
        {showId && <Button render={<Link href={`/dashboard/shows/${showId}`} />}>Open show</Button>}
      </main>
    </>
  );
}
