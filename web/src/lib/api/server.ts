import { cookies } from "next/headers";
import type { components } from "@/lib/api/api";

export type DashboardSummary = components["schemas"]["DashboardSummary"];

function apiBaseUrl(): string {
  return process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
}

/** Server-side fetch that forwards session cookies to the FastAPI backend. */
export async function fetchDashboardSummary(): Promise<DashboardSummary | null> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
  if (!cookieHeader) {
    return null;
  }

  const res = await fetch(`${apiBaseUrl()}/api/v1/dashboard/summary`, {
    headers: { Cookie: cookieHeader },
    cache: "no-store",
  });
  if (!res.ok) {
    return null;
  }
  return (await res.json()) as DashboardSummary;
}
