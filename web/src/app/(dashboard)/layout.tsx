"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { registerLogoutHandler } from "@/lib/api/middlewares";
import { UserProvider, useUser } from "@/components/providers/user-provider";
import { FeaturesProvider } from "@/components/providers/features-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { PageLoader } from "@/components/ui/page-loader";
import { AppSidebar } from "@/components/nav/app-sidebar";

function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, isLoading } = useUser();
  const verifyToastShown = React.useRef(false);

  // Register the handler once. Read the latest router via ref so a hypothetical
  // router identity change doesn't re-register on every render.
  const routerRef = React.useRef(router);
  routerRef.current = router;
  React.useEffect(() => {
    registerLogoutHandler(() => routerRef.current.push("/login"));
  }, []);

  React.useEffect(() => {
    if (isLoading) return;
    if (!user) {
      router.push("/login");
      return;
    }
    if (!user.is_verified && !verifyToastShown.current) {
      verifyToastShown.current = true;
      toast.info("Your account requires verification. Redirecting...");
      router.push("/login/verify");
    }
  }, [user, isLoading, router]);

  if (isLoading || !user) {
    return <PageLoader fullscreen />;
  }

  return (
    <FeaturesProvider>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset>{children}</SidebarInset>
      </SidebarProvider>
    </FeaturesProvider>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <UserProvider>
      <AuthGate>{children}</AuthGate>
    </UserProvider>
  );
}
