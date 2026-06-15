export const dynamic = "force-static";

export async function generateStaticParams() {
  return [{ showId: "_shell" }];
}

export default function ShowLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
