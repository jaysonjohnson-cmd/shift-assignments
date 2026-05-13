"use client";

import Image from "next/image";
import Link from "next/link";
import { Sidebar } from "./Sidebar";
import { UserMenu } from "./UserMenu";
import { ThemeToggle } from "./ThemeToggle";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-storesight-bg text-storesight-ink dark:bg-storesight-bg-dark dark:text-storesight-ink-dark">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-30 flex items-center gap-4 border-b border-storesight-border bg-storesight-surface/80 px-6 py-3 backdrop-blur-md dark:border-storesight-border-dark dark:bg-storesight-surface-dark/80">
          <Link href="/" className="flex items-center gap-2">
            <Image
              src="/brand/Storesight_primary.png"
              alt="Storesight"
              width={140}
              height={28}
              priority
              style={{ width: "auto", height: 28 }}
              className="dark:hidden"
            />
            <Image
              src="/brand/Storesight_primary.png"
              alt="Storesight"
              width={140}
              height={28}
              priority
              style={{ width: "auto", height: 28 }}
              className="hidden brightness-0 invert dark:block"
            />
          </Link>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>
        <main className="storesight-grid-bg flex min-w-0 flex-1 flex-col">
          {children}
        </main>
      </div>
    </div>
  );
}
