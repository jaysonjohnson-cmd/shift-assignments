"use client";

import Link from "next/link";
import { useUser } from "@/lib/useUser";
import type { Role } from "@/lib/api";

type Tile = {
  href: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  accent: string;
  /** Roles for whom this tile is enabled. */
  enabledFor: Role[];
  /** If false, render as a disabled card. */
  comingSoon?: boolean;
};

const ICON_CLASS = "h-6 w-6";

const tiles: Tile[] = [
  {
    href: "/assignments",
    title: "Assignments",
    description:
      "Pull live jobs from Bloom and generate morning / afternoon shift assignments for the review team.",
    accent: "from-storesight-primary/10 to-storesight-accent/10",
    enabledFor: ["admin", "reviewer", "viewer"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
        <path
          d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path d="M14 3v5h5M9 13h6M9 16.5h6M9 10h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    href: "/team-assignments",
    title: "Team Assignments",
    description:
      "View all jobs assigned to the team in the current shift with detailed status.",
    accent: "from-storesight-violet/15 to-storesight-lilac/15",
    enabledFor: ["admin", "viewer"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
        <path
          d="M17 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path d="M9 10h6M9 14h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    href: "/settings",
    title: "Settings",
    description:
      "Manage the roster of reviewers and admins who can sign in and be assigned shifts.",
    accent: "from-storesight-lilac/15 to-storesight-violet/15",
    enabledFor: ["admin", "reviewer", "viewer"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" />
        <path
          d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h.2a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.2a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    href: "/my-tasks",
    title: "My Tasks",
    description:
      "See the rows assigned to you in the latest published shift.",
    accent: "from-emerald-400/15 to-storesight-mint/20",
    enabledFor: ["reviewer", "admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
        <path
          d="m4 12 4 4 12-12"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
];

export default function HomePage() {
  const { user, role, loading } = useUser();

  const greeting = (() => {
    const firstName =
      user?.name?.split(" ")[0] || user?.email?.split("@")[0] || "there";
    return firstName;
  })();

  const visibleTiles = tiles.filter((t) => t.enabledFor.includes(role));

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
          {loading ? "Welcome" : `Welcome, ${greeting}`}
        </h1>
        <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Jump into the QC shift-assignment workflow. Pick a tile below.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {visibleTiles.map((tile) => {
          const content = (
            <div
              className={`group relative flex h-full flex-col overflow-hidden rounded-2xl border border-storesight-border bg-gradient-to-br ${tile.accent} p-5 transition dark:border-storesight-border-dark ${
                tile.comingSoon
                  ? "opacity-70"
                  : "hover:-translate-y-0.5 hover:border-storesight-accent/60 hover:shadow-lg"
              }`}
            >
              <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-storesight-surface text-storesight-primary shadow-sm dark:bg-storesight-surface-raised-dark dark:text-storesight-accent-light">
                {tile.icon}
              </div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                  {tile.title}
                </h2>
                {tile.comingSoon && (
                  <span className="rounded-full bg-storesight-bg-tint px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-storesight-primary dark:bg-storesight-accent/25 dark:text-storesight-accent-light">
                    Soon
                  </span>
                )}
              </div>
              <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                {tile.description}
              </p>
            </div>
          );
          return tile.comingSoon ? (
            <div key={tile.href} aria-disabled>
              {content}
            </div>
          ) : (
            <Link key={tile.href} href={tile.href}>
              {content}
            </Link>
          );
        })}
      </div>

      {role === "viewer" && !loading && (
        <p className="mt-8 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          You&apos;re signed in as a <strong>Viewer</strong>. Ask an admin to add
          you as a reviewer if you need to be assigned shifts.
        </p>
      )}
    </div>
  );
}
