"use client";

import { useState } from "react";
import { markTaskDone, unmarkTaskDone, ApiError } from "@/lib/api";
import type { Row } from "@/lib/types";

type Props = {
  row: Row;
  onChange: (completedAt: string | null) => void;
  size?: "sm" | "md";
  /** `default` keeps the legacy outlined button. `ghost` renders an icon-only subtle action. */
  variant?: "default" | "ghost";
  /** Called after the underlying API call succeeds, before onChange fires. Lets the parent play an exit animation. */
  onBeforeChange?: () => Promise<void> | void;
  /** Called when marking done is blocked by unreviewed responses (409 conflict). */
  onBlocked?: (jobId: string, unreviewed: number) => void;
  /** Disable the button (used by parent when in processing state). */
  disabled?: boolean;
  /** Called when processing state changes (true when processing starts/ends). */
  onProcessingChange?: (isProcessing: boolean) => void;
};

type ProcessingState = "idle" | "processing" | "approved";

export function MarkDoneButton({
  row,
  onChange,
  size = "md",
  variant = "default",
  onBeforeChange,
  onBlocked,
  disabled: externalDisabled,
  onProcessingChange,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [processingState, setProcessingState] = useState<ProcessingState>("idle");
  // Set to the remaining unreviewed count when completion is blocked, so we can
  // offer a "mark done anyway" override (e.g. responses unreviewable via the
  // FieldAgent alt-picture bug).
  const [blocked, setBlocked] = useState<number | null>(null);
  const jobId = row.jobId || row.id;
  const isDone = !!row.completedAt;

  const setProcessing = (state: ProcessingState) => {
    setProcessingState(state);
    if (onProcessingChange) {
      onProcessingChange(state !== "idle");
    }
  };

  const complete = async (override: boolean) => {
    setBusy(true);
    setError(null);
    setProcessing("processing");
    const completedAt = new Date().toISOString();
    try {
      await markTaskDone(jobId, undefined, override);
      // Show success state
      setProcessing("approved");
      setBlocked(null);
      if (onBeforeChange) await onBeforeChange();
      // Wait a moment to show the approved state, then remove from list
      await new Promise((resolve) => setTimeout(resolve, 1200));
      onChange(completedAt);
      setProcessing("idle");
    } catch (e) {
      const unreviewed =
        e instanceof ApiError && e.status === 409
          ? (e.data as { unreviewed?: number } | null)?.unreviewed
          : undefined;
      if (typeof unreviewed === "number") {
        // Notify parent to show blocked modal
        if (onBlocked) onBlocked(jobId, unreviewed);
        setProcessing("idle");
      } else {
        // Handle actual errors
        setError(e instanceof Error ? e.message : "Failed");
        setProcessing("idle");
      }
    } finally {
      setBusy(false);
    }
  };

  const handleClick = async () => {
    if (isDone) {
      setBusy(true);
      setError(null);
      try {
        await unmarkTaskDone(jobId);
        onChange(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed");
      } finally {
        setBusy(false);
      }
      return;
    }
    await complete(false);
  };

  const blockedConfirm = blocked !== null && (
    <div className="max-w-[230px] rounded-md border border-[#FFA500]/40 bg-[#FFA500]/10 px-2 py-1.5 text-[11px] text-[#B26A00] dark:text-[#FFA500]">
      <div>
        {blocked} unreviewed response{blocked === 1 ? "" : "s"} still showing on this
        job.
      </div>
      <div className="mt-1 text-[10px] leading-snug opacity-90">
        If you can&apos;t see them in Review, they were likely auto-rejected (e.g.
        distance) — clear the auto-rejects in FieldAgent, then mark done.
      </div>
      <div className="mt-1 flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => complete(true)}
          className="font-semibold underline hover:no-underline disabled:opacity-50"
        >
          Mark done anyway
        </button>
        <button
          type="button"
          onClick={() => setBlocked(null)}
          className="text-storesight-ink-muted hover:text-storesight-ink dark:text-storesight-ink-muted-dark"
        >
          Cancel
        </button>
      </div>
    </div>
  );

  if (variant === "ghost") {
    return (
      <div className="inline-flex flex-col items-end gap-1">
        <button
          type="button"
          onClick={handleClick}
          disabled={busy || externalDisabled || processingState !== "idle"}
          aria-label={isDone ? "Undo mark done" : "Mark done"}
          title={
            processingState === "processing"
              ? "Processing approval..."
              : processingState === "approved"
                ? "Approved!"
                : isDone
                  ? "Undo"
                  : "Mark done"
          }
          className={`inline-flex h-7 w-7 items-center justify-center rounded-full border text-storesight-ink-muted transition hover:scale-110 disabled:opacity-50 dark:text-storesight-ink-muted-dark ${
            processingState === "processing"
              ? "border-storesight-accent/60 bg-storesight-accent/15 text-storesight-accent dark:border-storesight-accent-light/60 dark:text-storesight-accent-light"
              : processingState === "approved"
                ? "border-emerald-400/60 bg-emerald-400/15 text-emerald-400"
                : isDone
                  ? "border-emerald-400/60 bg-emerald-400/15 text-emerald-400 hover:border-emerald-400 hover:text-emerald-400"
                  : "border-storesight-border hover:border-emerald-400 hover:text-emerald-400 dark:border-storesight-border-dark"
          }`}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
            {processingState === "processing" ? (
              <circle
                cx="12"
                cy="12"
                r="8"
                stroke="currentColor"
                strokeWidth="2"
                fill="none"
                className="animate-spin"
              />
            ) : processingState === "approved" || isDone ? (
              <path
                d="m5 12 4 4 10-10"
                stroke="currentColor"
                strokeWidth={processingState === "approved" ? "2.2" : "2"}
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
          <span className="max-w-[160px] rounded bg-storesight-hot-pink/15 px-1.5 py-0.5 text-[11px] font-medium text-storesight-hot-pink">
            {error}
          </span>
        )}
        {blockedConfirm}
      </div>
    );
  }

  const sizeClass =
    size === "sm"
      ? "px-2.5 py-1 text-[11px]"
      : "px-3 py-1.5 text-xs";

  const baseClass =
    processingState === "processing"
      ? "border-storesight-accent bg-storesight-accent/10 text-storesight-accent hover:border-storesight-accent/80 dark:border-storesight-accent-light/60 dark:bg-storesight-accent-light/10 dark:text-storesight-accent-light dark:hover:border-storesight-accent-light"
      : processingState === "approved"
        ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:border-emerald-400 dark:border-emerald-400/40 dark:bg-emerald-400/10 dark:text-emerald-300"
        : isDone
          ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:border-emerald-400 dark:border-emerald-400/40 dark:bg-emerald-400/10 dark:text-emerald-300"
          : "border-storesight-border bg-white text-storesight-primary-dark hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light";

  const isDisabled = busy || externalDisabled || processingState !== "idle";

  return (
    <div className="inline-flex flex-col items-start gap-0.5">
      <button
        type="button"
        onClick={handleClick}
        disabled={isDisabled}
        className={`inline-flex items-center gap-1.5 rounded-lg border font-medium transition disabled:opacity-60 ${baseClass} ${sizeClass}`}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
          {processingState === "processing" ? (
            <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.6" className="animate-spin" />
          ) : processingState === "approved" || isDone ? (
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
        {processingState === "processing"
          ? "Processing…"
          : processingState === "approved"
            ? "Approved"
            : busy
              ? "…"
              : isDone
                ? "Done · undo"
                : "Mark done"}
      </button>
      {error && (
        <span className="text-[10px] text-storesight-hot-pink">{error}</span>
      )}
      {blockedConfirm}
    </div>
  );
}
