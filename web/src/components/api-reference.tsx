"use client";

import { ApiReferenceReact } from "@scalar/api-reference-react";
import "@scalar/api-reference-react/style.css";
import { useTheme } from "next-themes";

/**
 * Interactive Scalar API reference.
 *
 * Self-hosted docs load the running instance schema (including config-gated
 * endpoints). GitHub Pages uses the committed project schema, which can
 * differ from an installed version or configuration. The bundle is shipped
 * in the static export — no CDN, works offline. Dark mode follows the site
 * theme.
 */
export default function ApiReference() {
  const { resolvedTheme } = useTheme();
  // Same base the rest of the SPA uses; empty string = same-origin (prod),
  // honors BASE_PATH/NEXT_PUBLIC_API_URL exactly like the api client.
  const base = process.env.NEXT_PUBLIC_API_URL || "";
  // Explicit override (set by the Pages docs build) wins; otherwise fetch the
  // live spec from the backend so it matches the deployed instance.
  const specUrl = process.env.NEXT_PUBLIC_OPENAPI_URL || `${base}/openapi.json`;

  return (
    <ApiReferenceReact
      configuration={{
        url: specUrl,
        darkMode: resolvedTheme === "dark",
        hideDarkModeToggle: true,
        // Disable the Scalar "Ask AI" agent chat (on by default on localhost).
        agent: { disabled: true },
      }}
    />
  );
}
