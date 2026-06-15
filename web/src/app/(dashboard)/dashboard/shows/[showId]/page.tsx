import ClientPage from "./client-page";

export const dynamic = "force-static";
export const dynamicParams = false;

// We emit a single SPA shell (`_shell/index.html`). In production the FastAPI
// 404 handler rewrites UUID paths to it; in dev the rewrites in
// `next.config.ts` do the same so any UUID URL hits this shell.
export async function generateStaticParams() {
  return [{ showId: "_shell" }];
}

export default function Page() {
  return <ClientPage />;
}
