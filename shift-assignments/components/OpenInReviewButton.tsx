"use client";

import { useState } from "react";
import { collectionReviewUrl } from "@/lib/reviewLink";
import type { Row } from "@/lib/types";

type Props = {
  row: Row;
  /** Visual size. */
  size?: "sm" | "md";
  /** Visual variant. `default` keeps legacy outlined look; `primary` is a solid CTA. */
  variant?: "default" | "primary";
  /** Disable the button (used when marking task done is processing). */
  disabled?: boolean;
};

function buildClipboardPayload(row: Row): string {
  if (row.jobId) return `Job ID: ${row.jobId}`;
  return `${row.projectId || row.id}`;
}

export function OpenInReviewButton({ row, size = "md", variant = "default", disabled = false }: Props) {
  const [copied, setCopied] = useState(false);

  const handleClick = async () => {
    if (disabled) return;
    const payload = buildClipboardPayload(row);
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard permission denied — still open the tab.
    }
    window.open(collectionReviewUrl(row), "_blank", "noopener,noreferrer");
  };

  const sizeClass =
    size === "sm"
      ? "px-2.5 py-1 text-[11px]"
      : "px-3 py-1.5 text-xs";

  const variantClass =
    variant === "primary"
      ? disabled
        ? "border-transparent bg-storesight-primary/50 text-white/70 shadow-sm dark:bg-storesight-accent/50 dark:text-storesight-bg-dark/70 dark:shadow-none"
        : "border-transparent bg-storesight-primary text-white shadow-sm shadow-storesight-primary/30 hover:bg-storesight-primary-dark dark:bg-storesight-accent dark:text-storesight-bg-dark dark:hover:bg-storesight-accent-light"
      : disabled
        ? "border-storesight-border/60 bg-white/60 text-storesight-primary-dark/60 dark:border-storesight-border-dark/60 dark:bg-storesight-surface-raised-dark/60 dark:text-storesight-ink-dark/60"
        : "border-storesight-border bg-white text-storesight-primary-dark hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light";

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      title={
        disabled
          ? "Processing approval — wait until complete before opening"
          : `Opens Collection Review in a new tab and copies Project ${row.projectId || row.id} to clipboard`
      }
      className={`inline-flex items-center gap-1.5 rounded-lg border font-medium transition disabled:cursor-not-allowed ${variantClass} ${sizeClass}`}
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {copied ? "Copied — paste in tab" : "Open in Review"}
    </button>
  );
}
