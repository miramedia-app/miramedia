/** Pull FastAPI `{ detail: string }` out of an openapi-fetch error payload. */
export function apiErrorDetail(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return fallback;
}
