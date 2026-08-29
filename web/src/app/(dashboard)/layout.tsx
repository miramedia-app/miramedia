"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { registerLogoutHandler } from "@/lib/api/middlewares";
import { authCoordinator, authTransition } from "@/lib/auth-generation";
import { hardNavigate, resetAuthCache } from "@/lib/auth";
import { UserProvider, useUser } from "@/components/providers/user-provider";
import { FeaturesProvider } from "@/components/providers/features-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { PageLoader } from "@/components/ui/page-loader";
import { AppSidebar } from "@/components/nav/app-sidebar";
import { MobileTabBar } from "@/components/nav/mobile-tab-bar";

function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { user, isLoading } = useUser();
  const verifyToastShown = React.useRef(false);

  // Register the handler once.
  // A 401 (session expiry) is an auth exit just like an explicit logout: drop the
  // shared cache before redirecting, or the expired user's identity and data stay
  // warm for whoever logs in next in this tab.
  const qcRef = React.useRef(qc);
  qcRef.current = qc;
  React.useEffect(() => {
    registerLogoutHandler(async (token) => {
      // Blank the authenticated tree BEFORE clearing: `clear()` leaves mounted
      // observers holding their last result, so this is what actually stops the
      // expired admin's identity and privileged UI from staying on screen.
      authTransition.begin();
      try {
        await resetAuthCache(qcRef.current);
      } finally {
        // A login may have won while we were clearing. Its generation supersedes
        // ours, so skip the navigation rather than bouncing the new account to
        // /login. The coordinator guarantees exactly one exit at a time.
        if (authCoordinator.isCurrent(token)) {
          // Full document load so no observer from the dead session survives.
          hardNavigate("/login");
        }
      }
    });
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
        <SidebarInset className="pb-[calc(3.5rem+env(safe-area-inset-bottom))] lg:pb-0">
          {children}
        </SidebarInset>
        <MobileTabBar />
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
