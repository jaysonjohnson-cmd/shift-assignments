"use client";

import { useTheme } from "@/lib/useTheme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      title={isDark ? "Light mode" : "Dark mode"}
      className="flex h-9 w-9 items-center justify-center rounded-xl border border-transparent text-storesight-ink-muted transition hover:border-storesight-border hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:border-storesight-border-dark dark:hover:text-storesight-accent-light"
    >
      {isDark ? (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
          <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.6" />
          <path
            d="M12 2v2m0 16v2m10-10h-2M4 12H2m15.5-6.5-1.5 1.5m-10 10L4.5 18.5m13 0-1.5-1.5m-10-10L4.5 5.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M20.8 14.5A8.5 8.5 0 0 1 9.5 3.2a1 1 0 0 0-1.3-1.3 10.5 10.5 0 1 0 13.9 13.9 1 1 0 0 0-1.3-1.3Z"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}
