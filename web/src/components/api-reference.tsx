"use client";

import { ApiReferenceReact } from "@scalar/api-reference-react";
import "@scalar/api-reference-react/style.css";
import { useTheme } from "next-themes";

/**
 * Live, interactive Scalar API reference.
 *
 * Schema is fetched at runtime from the running backend so it always matches
 * the deployed instance (including config-gated endpoints). The bundle is
 * shipped in the static export — no CDN, works offline. Dark mode follows the
 * site theme.
 */
export default function ApiReference() {
  const { resolvedTheme } = useTheme();
  // Same base the rest of the SPA uses; empty string = same-origin (prod),
  // honors BASE_PATH/NEXT_PUBLIC_API_URL exactly like the api client.
  const base = process.env.NEXT_PUBLIC_API_URL || "";

  return (
    <ApiReferenceReact
      configuration={{
        url: `${base}/openapi.json`,
        darkMode: resolvedTheme === "dark",
        hideDarkModeToggle: true,
        // Disable the Scalar "Ask AI" agent chat (on by default on localhost).
        agent: { disabled: true },
      }}
    />
  );
}
