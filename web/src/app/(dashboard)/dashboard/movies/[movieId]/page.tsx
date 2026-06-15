import ClientPage from "./client-page";

export const dynamic = "force-static";
export const dynamicParams = false;

export async function generateStaticParams() {
  return [{ movieId: "_shell" }];
}

export default function Page() {
  return <ClientPage />;
}
