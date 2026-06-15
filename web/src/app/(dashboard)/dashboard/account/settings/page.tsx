"use client";

import { DashboardHeader } from "@/components/dashboard-header";
import { UserSettings } from "@/components/account/user-settings";
import { ApiTokens } from "@/components/account/api-tokens";

export default function AccountSettingsPage() {
  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Account", href: "/dashboard/account/settings" },
          { label: "Settings" },
        ]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        <UserSettings />
        <ApiTokens />
      </main>
    </>
  );
}
