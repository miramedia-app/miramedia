"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

export default function SystemPage() {
  const router = useRouter();
  React.useEffect(() => {
    router.replace("/dashboard/system/users");
  }, [router]);
  return <p className="p-4 text-sm text-muted-foreground">Redirecting...</p>;
}
