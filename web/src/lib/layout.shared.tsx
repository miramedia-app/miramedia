import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";
import { Logo } from "@/components/logo";

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <>
          <Logo className="size-5 text-foreground" />
          MiraMedia Docs
        </>
      ),
      url: "/docs",
    },
    githubUrl: "https://github.com/miramedia-app/miramedia",
  };
}
