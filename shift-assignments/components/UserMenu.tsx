"use client";

import { useEffect, useRef, useState } from "react";
import { useUser } from "@/lib/useUser";
import type { Role } from "@/lib/api";

function initials(name: string, email: string) {
  const source = (name || email || "?").trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

function roleLabel(role: Role): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

export function UserMenu() {
  const { user, role, loading } = useUser();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  if (loading) {
    return (
      <div className="h-9 w-40 animate-pulse rounded-xl bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark" />
    );
  }

  if (!user) {
    return (
      <a
        href="/logout"
        className="rounded-lg border border-storesight-border px-3 py-1.5 text-sm text-storesight-ink-muted hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark"
      >
        Sign in
      </a>
    );
  }

  const display = user.name || user.email;
  const roleTone =
    role === "admin"
      ? "bg-storesight-accent/15 text-storesight-primary dark:bg-storesight-accent/25 dark:text-storesight-accent-light"
      : role === "lead"
        ? "bg-storesight-sun/20 text-amber-700 dark:bg-storesight-sun/15 dark:text-amber-300"
        : role === "reviewer"
          ? "bg-storesight-mint/25 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
          : "bg-storesight-bg-tint text-storesight-ink-muted dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark";

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-xl border border-transparent bg-storesight-surface px-2 py-1.5 text-sm transition hover:border-storesight-border dark:bg-storesight-surface-raised-dark dark:hover:border-storesight-border-dark"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-storesight-accent text-xs font-semibold text-white">
          {initials(user.name, user.email)}
        </span>
        <span className="hidden text-left md:block">
          <span className="block text-sm font-medium text-storesight-ink dark:text-storesight-ink-dark">
            {display}
          </span>
          <span
            className={`mt-0.5 inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${roleTone}`}
          >
            {roleLabel(role)}
          </span>
        </span>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          className="text-storesight-ink-muted"
          aria-hidden
        >
          <path
            d="m6 9 6 6 6-6"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-2 w-64 overflow-hidden rounded-xl border border-storesight-border bg-white shadow-xl dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark"
        >
          <div className="border-b border-storesight-border px-4 py-3 dark:border-storesight-border-dark">
            <div className="text-sm font-semibold text-storesight-ink dark:text-storesight-ink-dark">
              {user.name || "(no name)"}
            </div>
            <div className="truncate text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              {user.email}
            </div>
            <div
              className={`mt-2 inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${roleTone}`}
            >
              {roleLabel(role)}
            </div>
          </div>
          <a
            href="/logout"
            className="block px-4 py-2.5 text-sm text-storesight-ink-muted hover:bg-storesight-bg-tint hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:bg-storesight-surface-dark dark:hover:text-storesight-accent-light"
          >
            Sign out
          </a>
        </div>
      )}
    </div>
  );
}
