"use client";

import dynamic from "next/dynamic";

// Scalar touches browser APIs and ships a large bundle: load client-only,
// code-split off the rest of the docs site. `ssr: false` is valid here
// because this file is a Client Component.
const ApiReference = dynamic(() => import("@/components/api-reference"), {
  ssr: false,
  loading: () => <div className="text-fd-muted-foreground p-8 text-sm">Loading API reference…</div>,
});

// Full-bleed: render outside the fumadocs prose/TOC container so Scalar's own
// layout and styles don't fight the docs typography. Size to the exact docs
// content area (same vars fumadocs' sticky sidebar uses) and contain scroll
// inside Scalar so the outer page never scrolls — otherwise the sticky
// sidebar drifts with page scroll.
export default function ApiReferencePage() {
  return (
    <div className="h-[calc(var(--fd-docs-height,100dvh)-var(--fd-docs-row-1,0px))] min-h-0 w-full overflow-x-hidden overflow-y-auto overscroll-contain">
      <ApiReference />
    </div>
  );
}
