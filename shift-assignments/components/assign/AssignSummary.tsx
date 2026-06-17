"use client";

import Link from "next/link";
import type { Reviewer, Row } from "@/lib/types";

export type SummaryLine = {
  reviewerId: string;
  reviewerName: string;
  shiftLabel: string;
  count: number;
  firstPriority?: number;
  lastPriority?: number;
};

export function AssignSummary({
  title,
  lines,
  overflow,
  onBack,
}: {
  title: string;
  lines: SummaryLine[];
  overflow: number;
  onBack: () => void;
}) {
  return (
    <div className="px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
        {title}
      </h1>
      <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
        {lines.length} reviewer assignment{lines.length === 1 ? "" : "s"} · {overflow} task
        {overflow === 1 ? "" : "s"} in overflow
      </p>

      <div className="mt-6 overflow-hidden rounded-2xl border border-storesight-border bg-white dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <table className="w-full text-sm">
          <thead className="bg-storesight-bg-tint text-[11px] uppercase tracking-wide text-storesight-ink-muted dark:bg-storesight-surface-raised-dark/50 dark:text-storesight-ink-muted-dark">
            <tr>
              <th className="px-4 py-2 text-left">Reviewer</th>
              <th className="px-4 py-2 text-left">Shift</th>
              <th className="px-4 py-2 text-right">Count</th>
              <th className="px-4 py-2 text-right">Priority range</th>
            </tr>
          </thead>
          <tbody>
            {lines.length === 0 ? (
              <tr>
                <td
                  colSpan={4}
                  className="px-4 py-6 text-center text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark"
                >
                  Nothing was assigned.
                </td>
              </tr>
            ) : (
              lines.map((l, i) => (
                <tr
                  key={`${l.reviewerId}-${l.shiftLabel}-${i}`}
                  className="border-t border-storesight-border dark:border-storesight-border-dark"
                >
                  <td className="px-4 py-2 text-storesight-ink dark:text-storesight-ink-dark">
                    {l.reviewerName}
                  </td>
                  <td className="px-4 py-2 text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                    {l.shiftLabel}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">{l.count}</td>
                  <td className="px-4 py-2 text-right tabular-nums text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                    {l.firstPriority != null && l.lastPriority != null
                      ? `#${l.firstPriority}–${l.lastPriority}`
                      : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-6 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-storesight-accent bg-storesight-accent/10 px-4 py-2 text-sm font-semibold text-storesight-primary transition hover:bg-storesight-accent/20 dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
        >
          Assign another shift
        </button>
        <Link
          href="/my-tasks"
          className="rounded-lg border border-storesight-border bg-white px-4 py-2 text-sm font-medium text-storesight-ink hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
        >
          View My Tasks
        </Link>
      </div>
    </div>
  );
}

export function summarizeShift(
  shiftLabel: string,
  assignments: Record<string, Row[]>,
  reviewers: Reviewer[],
): SummaryLine[] {
  const reviewerById = new Map(reviewers.map((r) => [r.id, r]));
  const lines: SummaryLine[] = [];
  for (const [reviewerId, rows] of Object.entries(assignments)) {
    if (rows.length === 0) continue;
    const r = reviewerById.get(reviewerId);
    lines.push({
      reviewerId,
      reviewerName: r?.name ?? reviewerId,
      shiftLabel,
      count: rows.length,
      firstPriority: rows[0]?.priority,
      lastPriority: rows[rows.length - 1]?.priority,
    });
  }
  return lines;
}
