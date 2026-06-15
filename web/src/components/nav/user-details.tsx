"use client";

import { useUser } from "@/components/providers/user-provider";

export function UserDetails() {
  const { user } = useUser();
  if (!user) return null;
  return (
    <>
      <span className="truncate font-semibold">{user.email}</span>
      <span className="truncate text-xs">{user.is_superuser ? "Administrator" : "User"}</span>
    </>
  );
}
