"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getBloomJobs } from "@/lib/api";
import type { Row } from "@/lib/types";

type AgedRow = Row & { daysOld: number };

const PRESETS = [
  { label: "All aged", min: 0 },
  { label: "7+ days", min: 7 },
  { label: "14+ days", min: 14 },
  { label: "30+ days", min: 30 },
];

function parseMDY(s: string): Date | null {
  // "MM/DD/YYYY" → Date
  if (!s) return null;
  const [m, d, y] = s.split("/").map(Number);
  if (!m || !d || !y) return null;
  return new Date(y, m - 1, d);
}

function daysAgo(s: string): number {
  const d = parseMDY(s);
  if (!d) return 0;
  return Math.floor((Date.now() - d.getTime()) / 86_400_000);
}

function AgeBadge({ days }: { days: number }) {
  let cls = "bg-storesight-bg-tint text-storesight-ink-muted dark:bg-storesight-accent/20 dark:text-storesight-accent-light";
  if (days >= 30) cls = "bg-[#FF4D4D]/15 text-[#FF4D4D]";
  else if (days >= 14) cls = "bg-[#FFA500]/15 text-[#FFA500]";
  else if (days >= 7) cls = "bg-yellow-400/15 text-yellow-600 dark:text-yellow-400";
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-bold tabular-nums ${cls}`}>
      {days === 0 ? "<1d" : `${days}d`}
    </span>
  );
}

function buildResponseSearchUrl(jobId: string, projectId: string): string {
  const params = new URLSearchParams({ job_id: jobId, resp_status: "N" });
  return `https://prod.fieldagent.net/admin/fieldagent/responseSearch/?${params.toString()}`;
}

function buildCollectionReviewUrl(jobId: string, projectId: string): string {
  const params = new URLSearchParams({ job: jobId, project: projectId });
  return `https://prod.fieldagent.net/admin/fieldagent/collection-review/?${params.toString()}#/`;
}

const PRIORITY_META: Record<number, { tint: string; text: string; label: string }> = {
  1: { label: "P1", tint: "bg-[#FF4D4D]/15", text: "text-[#FF4D4D]" },
  2: { label: "P2", tint: "bg-[#FFA500]/15", text: "text-[#FFA500]" },
  3: { label: "P3", tint: "bg-[#3B82F6]/15", text: "text-[#3B82F6]" },
};
function priorityMeta(p: number) {
  return PRIORITY_META[p] ?? { label: `P${p}`, tint: "bg-storesight-bg-tint", text: "text-storesight-primary" };
}

export default function AgedJobsPage() {
  const [rows, setRows] = useState<AgedRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [minDays, setMinDays] = useState(0);
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const jobs = await getBloomJobs(true);
      const aged: AgedRow[] = jobs
        .filter((r) => Number(r.extras?.old_sub ?? 0) > 0)
        .map((r) => ({
          ...r,
          daysOld: daysAgo(String(r.extras?.startDate ?? "")),
        }));
      setRows(aged);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const filtered = rows
    .filter((r) => r.daysOld >= minDays)
    .sort((a, b) => sortDir === "desc" ? b.daysOld - a.daysOld : a.daysOld - b.daysOld);

  const oldest = rows.length > 0 ? Math.max(...rows.map((r) => r.daysOld)) : 0;

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            href="/"
            className="text-xs font-medium text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:text-storesight-accent-light"
          >
            ← Back to home
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
            Aged Submissions
          </h1>
          <p className="mt-0.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {loading
              ? "Loading…"
              : `${filtered.length} job${filtered.length !== 1 ? "s" : ""} with aged submissions${oldest > 0 ? ` · oldest started ${oldest} days ago` : ""}`}
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-lg border border-storesight-border bg-white px-3 py-2 text-sm font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {error}
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-lg border border-storesight-border bg-white p-1 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => setMinDays(p.min)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition ${
                minDays === p.min
                  ? "bg-storesight-accent/20 text-storesight-primary dark:text-storesight-accent-light"
                  : "text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
          className="inline-flex items-center gap-1.5 rounded-lg border border-storesight-border bg-white px-3 py-1.5 text-xs font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
        >
          {sortDir === "desc" ? "Oldest first" : "Newest first"}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Loading…
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white/60 px-6 py-12 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-dark/60 dark:text-storesight-ink-muted-dark">
          No jobs match this filter.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-storesight-border dark:border-storesight-border-dark">
          <table className="w-full text-sm">
            <thead className="border-b border-storesight-border bg-storesight-bg-tint dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Job</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Priority</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Age</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Unreviewed</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Links</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-storesight-border dark:divide-storesight-border-dark">
              {filtered.map((r) => {
                const meta = priorityMeta(r.priority);
                const startDate = String(r.extras?.startDate ?? "");
                return (
                  <tr
                    key={r.id}
                    className="bg-white transition hover:bg-storesight-bg-tint dark:bg-storesight-surface-dark dark:hover:bg-storesight-surface-raised-dark"
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium text-storesight-ink dark:text-storesight-ink-dark">
                        {r.name || `Job ${r.jobId}`}
                      </div>
                      <div className="mt-0.5 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        {r.projectId && <span>Project {r.projectId}</span>}
                        {r.jobId && <span className="ml-2">· Job {r.jobId}</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold ${meta.tint} ${meta.text}`}>
                        {meta.label}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <AgeBadge days={r.daysOld} />
                        {startDate && (
                          <span className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                            started {startDate}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums text-storesight-ink dark:text-storesight-ink-dark">
                      {r.unreviewedCount}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex items-center gap-2">
                        {r.jobId && r.projectId && (
                          <>
                            <a
                              href={buildCollectionReviewUrl(r.jobId, r.projectId)}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
                            >
                              Review
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
                            </a>
                            <a
                              href={buildResponseSearchUrl(r.jobId, r.projectId)}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
                            >
                              Responses
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
                            </a>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
