"use client";

import { useState } from "react";
import { markTaskDone, unmarkTaskDone } from "@/lib/api";
import type { Row } from "@/lib/types";

type Props = {
  row: Row;
  onChange: (completedAt: string | null) => void;
  size?: "sm" | "md";
  /** `default` keeps the legacy outlined button. `ghost` renders an icon-only subtle action. */
  variant?: "default" | "ghost";
  /** Called after the underlying API call succeeds, before onChange fires. Lets the parent play an exit animation. */
  onBeforeChange?: () => Promise<void> | void;
};

export function MarkDoneButton({
  row,
  onChange,
  size = "md",
  variant = "default",
  onBeforeChange,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const projectId = row.projectId || row.id;
  const isDone = !!row.completedAt;

  const handleClick = async () => {
    setBusy(true);
    setError(null);
    try {
      if (isDone) {
        await unmarkTaskDone(projectId);
        onChange(null);
      } else {
        await markTaskDone(projectId);
        if (onBeforeChange) await onBeforeChange();
        onChange(new Date().toISOString());
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  if (variant === "ghost") {
    return (
      <div className="inline-flex flex-col items-end gap-0.5">
        <button
          type="button"
          onClick={handleClick}
          disabled={busy}
          aria-label={isDone ? "Undo mark done" : "Mark done"}
          title={isDone ? "Undo" : "Mark done"}
          className={`inline-flex h-7 w-7 items-center justify-center rounded-full border text-storesight-ink-muted transition hover:scale-110 hover:border-emerald-400 hover:text-emerald-400 disabled:opacity-50 dark:text-storesight-ink-muted-dark ${
            isDone
              ? "border-emerald-400/60 bg-emerald-400/15 text-emerald-400"
              : "border-storesight-border dark:border-storesight-border-dark"
          }`}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
            {isDone ? (
              <path
                d="m5 12 4 4 10-10"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            ) : (
              <path
                d="m5 12 4 4 10-10"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}
          </svg>
        </button>
        {error && (
          <span className="text-[10px] text-storesight-hot-pink">{error}</span>
        )}
      </div>
    );
  }

  const sizeClass =
    size === "sm"
      ? "px-2.5 py-1 text-[11px]"
      : "px-3 py-1.5 text-xs";

  const baseClass = isDone
    ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:border-emerald-400 dark:border-emerald-400/40 dark:bg-emerald-400/10 dark:text-emerald-300"
    : "border-storesight-border bg-white text-storesight-primary-dark hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light";

  return (
    <div className="inline-flex flex-col items-start gap-0.5">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={`inline-flex items-center gap-1.5 rounded-lg border font-medium transition disabled:opacity-60 ${baseClass} ${sizeClass}`}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
          {isDone ? (
            <path
              d="m5 12 4 4 10-10"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : (
            <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.6" />
          )}
        </svg>
        {busy ? "…" : isDone ? "Done · undo" : "Mark done"}
      </button>
      {error && (
        <span className="text-[10px] text-storesight-hot-pink">{error}</span>
      )}
    </div>
  );
}
