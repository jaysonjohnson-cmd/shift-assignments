"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { enrichJobNames, type EnrichNamesProgress } from "@/lib/api";
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
  const progress = useEnrichmentLoop();
  return (
    <div className="px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
        {title}
      </h1>
      <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
        {lines.length} reviewer assignment{lines.length === 1 ? "" : "s"} · {overflow} task
        {overflow === 1 ? "" : "s"} in overflow
      </p>

      <EnrichmentBanner progress={progress} />

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

type EnrichState =
  | { kind: "idle" }
  | { kind: "running"; progress: EnrichNamesProgress }
  | { kind: "done"; progress: EnrichNamesProgress }
  | { kind: "error"; message: string };

function useEnrichmentLoop(): EnrichState {
  const [state, setState] = useState<EnrichState>({ kind: "idle" });

  useEffect(() => {
    let cancelled = false;
    let lastDone = -1;
    let stalledRounds = 0;
    const MAX_ROUNDS = 40; // safety cap (~17 min at 25s/round)

    (async () => {
      for (let i = 0; i < MAX_ROUNDS && !cancelled; i++) {
        try {
          const res = await enrichJobNames(25);
          if (cancelled) return;
          if (res.total === 0) {
            setState({ kind: "done", progress: res });
            return;
          }
          setState({ kind: "running", progress: res });
          if (res.done >= res.total) {
            setState({ kind: "done", progress: res });
            return;
          }
          // Break if we stop making progress (rate-limited + nothing to fetch).
          if (res.done === lastDone) {
            stalledRounds += 1;
            if (stalledRounds >= 2) {
              setState({ kind: "done", progress: res });
              return;
            }
          } else {
            stalledRounds = 0;
            lastDone = res.done;
          }
        } catch (e) {
          if (cancelled) return;
          setState({
            kind: "error",
            message: e instanceof Error ? e.message : "Enrichment failed",
          });
          return;
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

function EnrichmentBanner({ progress }: { progress: EnrichState }) {
  if (progress.kind === "idle") return null;
  if (progress.kind === "error") {
    return (
      <div className="mt-4 inline-block rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-3 py-1.5 text-xs text-storesight-hot-pink">
        Couldn&apos;t enrich job names: {progress.message}
      </div>
    );
  }
  const { done, total } = progress.progress;
  if (total === 0) return null;
  const pct = total === 0 ? 0 : Math.round((done / total) * 100);
  const isDone = progress.kind === "done";
  return (
    <div className="mt-4 inline-flex items-center gap-3 rounded-lg border border-storesight-border bg-white/60 px-3 py-2 text-xs text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark/60 dark:text-storesight-ink-muted-dark">
      {isDone ? (
        <span className="inline-flex items-center gap-1.5 font-medium text-emerald-700 dark:text-emerald-300">
          <span className="h-2 w-2 rounded-full bg-emerald-500" aria-hidden />
          Job names enriched ({done} of {total})
        </span>
      ) : (
        <>
          <span className="relative inline-flex h-2 w-2" aria-hidden>
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-storesight-accent opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-storesight-accent" />
          </span>
          <span>
            Enriching job names… <span className="tabular-nums">{done} / {total}</span> ({pct}%)
          </span>
        </>
      )}
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
