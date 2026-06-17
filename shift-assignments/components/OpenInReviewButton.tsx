"use client";

import { useState } from "react";
import type { Row } from "@/lib/types";

const MEDIA_REVIEW_URL =
  "https://my.fieldagent.net/admin/fieldagent/media-review-v3/";

type Props = {
  row: Row;
  /** Visual size. */
  size?: "sm" | "md";
  /** Visual variant. `default` keeps legacy outlined look; `primary` is a solid CTA. */
  variant?: "default" | "primary";
};

function buildClipboardPayload(row: Row): string {
  if (row.jobId) return `Job ID: ${row.jobId}`;
  return `${row.projectId || row.id}`;
}

function buildUrl(row: Row): string {
  if (!row.jobId) return MEDIA_REVIEW_URL;
  const params = new URLSearchParams({ job: row.jobId });
  const pid = row.projectId || row.id;
  if (pid) params.set("project", pid);
  return `${MEDIA_REVIEW_URL}?${params.toString()}#/`;
}

export function OpenInReviewButton({ row, size = "md", variant = "default" }: Props) {
  const [copied, setCopied] = useState(false);

  const handleClick = async () => {
    const payload = buildClipboardPayload(row);
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard permission denied — still open the tab.
    }
    window.open(buildUrl(row), "_blank", "noopener,noreferrer");
  };

  const sizeClass =
    size === "sm"
      ? "px-2.5 py-1 text-[11px]"
      : "px-3 py-1.5 text-xs";

  const variantClass =
    variant === "primary"
      ? "border-transparent bg-storesight-primary text-white shadow-sm shadow-storesight-primary/30 hover:bg-storesight-primary-dark dark:bg-storesight-accent dark:text-storesight-bg-dark dark:hover:bg-storesight-accent-light"
      : "border-storesight-border bg-white text-storesight-primary-dark hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light";

  return (
    <button
      type="button"
      onClick={handleClick}
      title={`Opens Media Review in a new tab and copies Project ${row.projectId || row.id} to clipboard`}
      className={`inline-flex items-center gap-1.5 rounded-lg border font-medium transition ${variantClass} ${sizeClass}`}
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
