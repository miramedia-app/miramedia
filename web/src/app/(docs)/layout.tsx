import { RootProvider } from "fumadocs-ui/provider/next";
import { DocsLayout } from "fumadocs-ui/layouts/docs";
import { source } from "@/lib/source";
import { baseOptions } from "@/lib/layout.shared";
import DocsSearchDialog from "@/components/docs-search";
// Docs-only CSS: kept out of globals.css so dashboard doesn't ship fumadocs styles.
import "./docs.css";

export default function DocsRouteLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <RootProvider search={{ SearchDialog: DocsSearchDialog }}>
      <DocsLayout tree={source.getPageTree()} {...baseOptions()}>
        {children}
      </DocsLayout>
    </RootProvider>
  );
}
