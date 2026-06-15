import { source } from "@/lib/source";
import { DocsBody, DocsDescription, DocsPage, DocsTitle } from "fumadocs-ui/layouts/docs/page";
import { notFound } from "next/navigation";
import { getMDXComponents } from "@/components/mdx";
import type { Metadata } from "next";
import { createRelativeLink } from "fumadocs-ui/mdx";

// `/docs/api-reference` is served by a dedicated full-bleed route
// (src/app/(docs)/docs/api-reference/page.tsx) that embeds Scalar. Keep it out
// of this catch-all so static export doesn't emit two pages for one path.
const RESERVED_ROUTES = new Set(["api-reference"]);

function isReserved(slug: string[] | undefined): boolean {
  return slug?.length === 1 && RESERVED_ROUTES.has(slug[0]);
}

export default async function Page(props: PageProps<"/docs/[[...slug]]">) {
  const params = await props.params;
  if (isReserved(params.slug)) notFound();
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const MDX = page.data.body;

  return (
    <DocsPage toc={page.data.toc} full={page.data.full}>
      <DocsTitle>{page.data.title}</DocsTitle>
      <DocsDescription>{page.data.description}</DocsDescription>
      <DocsBody>
        <MDX
          components={getMDXComponents({
            a: createRelativeLink(source, page),
          })}
        />
      </DocsBody>
    </DocsPage>
  );
}

export async function generateStaticParams() {
  return source.generateParams().filter((p) => !isReserved(p.slug));
}

export async function generateMetadata(props: PageProps<"/docs/[[...slug]]">): Promise<Metadata> {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  return {
    title: page.data.title,
    description: page.data.description,
  };
}
