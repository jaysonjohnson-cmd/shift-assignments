"use client";

import { useState } from "react";
import { checkSplitOpen } from "@/lib/api";
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

/** A teammate is still on this split job, so opening it is staggered. */
export function isLiveSplit(row: Row): boolean {
  return !!row.jobId && (row.splitPartners ?? []).some((p) => !p.done);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function OpenInReviewButton({ row, size = "md", variant = "default", disabled = false }: Props) {
  const [copied, setCopied] = useState(false);
  // Seconds left while a teammate's load registers (split jobs only).
  const [waiting, setWaiting] = useState<{ seconds: number; teammate: string } | null>(null);

  /**
   * Two sharers loading a split job at the same moment both get the same
   * responses — Collection Review's in-review hold only protects whoever loads
   * second. So a split job asks the server first and holds back while a
   * teammate's load registers.
   *
   * The tab opens immediately, on the click: browsers block a tab opened from
   * a timer, so it's opened blank with a note and pointed at Collection
   * Review once it's this reviewer's turn.
   */
  const openStaggered = async (url: string) => {
    const tab = window.open("", "_blank");
    if (tab) {
      tab.opener = null;
      tab.document.title = "Waiting for your teammate…";
      tab.document.body.style.cssText =
        "font-family:system-ui,sans-serif;padding:2rem;color:#4e339c";
      tab.document.body.textContent = "Checking whether your teammate is loading this job…";
    }
    try {
      // Re-ask after each wait: a third sharer may have slipped in meanwhile.
      for (let attempt = 0; attempt < 4; attempt++) {
        const check = await checkSplitOpen(row.jobId as string);
        if (!check.wait) break;
        const teammate = check.teammate || "Your teammate";
        if (tab) {
          tab.document.body.textContent =
            `${teammate} just opened this job. Opening yours in ${check.wait}s so you don't get the same responses — keep this tab open.`;
        }
        for (let s = check.wait; s > 0; s--) {
          setWaiting({ seconds: s, teammate });
          await sleep(1000);
        }
      }
    } catch {
      // Never stop someone working over this check — open anyway.
    } finally {
      setWaiting(null);
    }
    if (tab && !tab.closed) tab.location.href = url;
    else if (!tab) window.open(url, "_blank", "noopener,noreferrer");
  };

  const handleClick = async () => {
    if (disabled || waiting) return;
    const payload = buildClipboardPayload(row);
    const url = collectionReviewUrl(row);
    if (isLiveSplit(row)) {
      // Before any await, so the tab opens while the click still counts as a
      // user gesture.
      void openStaggered(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard permission denied — the tab is open regardless.
    }
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
      disabled={disabled || !!waiting}
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
      {waiting
        ? `${waiting.teammate} is loading · ${waiting.seconds}s`
        : copied
          ? "Copied — paste in tab"
          : "Open in Review"}
    </button>
  );
}
