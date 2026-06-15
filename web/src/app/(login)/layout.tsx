import Image from "next/image";
import Link from "next/link";
import { Logo } from "@/components/logo";
import { Separator } from "@/components/ui/separator";

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  const version = process.env.NEXT_PUBLIC_VERSION || "dev";
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="flex flex-col gap-4 p-6 md:p-10">
        <header className="flex justify-center gap-2 md:justify-start">
          <Link className="flex items-center gap-2" href="/">
            <div className="flex size-16 items-center justify-center rounded-md">
              <Logo className="size-12 text-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold">MiraMedia</h1>
              <span className="truncate text-xs">{version}</span>
            </div>
          </Link>
        </header>
        <main className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-[90vw]">{children}</div>
        </main>
        <div className="flex flex-col items-center justify-center gap-3 text-center">
          <a
            target="_blank"
            rel="noreferrer"
            className="underline"
            href="https://miramedia-app.github.io/miramedia/latest/troubleshooting/"
          >
            Trouble logging in?
          </a>
          <footer className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <a
              target="_blank"
              rel="noreferrer"
              className="underline"
              href="https://github.com/miramedia-app/miramedia"
            >
              GitHub
            </a>
            <Separator className="h-4" orientation="vertical" />
            <a
              target="_blank"
              rel="noreferrer"
              className="underline"
              href="https://unsplash.com/photos/a-black-and-white-photo-of-a-film-strip-ER_2eKPscTM"
            >
              Image Credit
            </a>
          </footer>
        </div>
      </div>
      <div className="relative hidden lg:block">
        <Image
          src="/images/login-background.jpg"
          alt="background"
          fill
          priority
          className="absolute inset-0 h-full w-full rounded-l-3xl object-cover dark:brightness-[0.8]"
        />
      </div>
    </div>
  );
}
