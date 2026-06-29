"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getBloomJobs, getSubmissionAges } from "@/lib/api";
import type { Row } from "@/lib/types";

type AgedRow = Row & { daysOld: number | null; oldestSubDate: string | null };

type SortMode = "urgency" | "closing" | "waiting" | "backlog";

const SORTS: { key: SortMode; label: string }[] = [
  { key: "urgency", label: "Urgency" },
  { key: "closing", label: "Closing soon" },
  { key: "waiting", label: "Longest waiting" },
  { key: "backlog", label: "Biggest backlog" },
];

const PRESETS = [
  { label: "All old", min: 0 },
  { label: "3+ days", min: 3 },
  { label: "7+ days", min: 7 },
  { label: "14+ days", min: 14 },
];

function isoToDaysAgo(iso: string): number {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 0;
  return Math.floor((Date.now() - d.getTime()) / 86_400_000);
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** Days until the job's end date. null when no/invalid date; negative = overdue. */
function daysUntilClose(r: Row): number | null {
  const raw = String(r.extras?.endDate ?? "");
  if (!raw) return null;
  const d = new Date(raw);
  if (isNaN(d.getTime())) return null;
  return Math.ceil((d.getTime() - Date.now()) / 86_400_000);
}

function pendingOf(r: Row): number {
  return Math.round(Number(r.extras?.pendingRatio ?? 0));
}

/**
 * Blended 0–100 urgency: how risky it is to leave this job's unreviewed work
 * sitting. Weights the hard deadline most, then how long it's waited, then how
 * much of the job is still pending. Deadline pressure comes from the job's end
 * date; the feed's `days_remaining` is ignored (its values don't match endDate).
 */
function closeScore(days: number | null): number {
  if (days === null) return 20; // unknown deadline — mild
  if (days < 0) return 100; // overdue
  if (days <= 3) return 100;
  if (days <= 7) return 85;
  if (days <= 14) return 65;
  if (days <= 30) return 40;
  return 15;
}

function waitScore(days: number | null): number {
  if (days === null) return 0;
  if (days >= 30) return 100;
  if (days >= 14) return 75;
  if (days >= 7) return 50;
  if (days >= 3) return 25;
  return 10;
}

function urgencyScore(r: AgedRow): number {
  const close = closeScore(daysUntilClose(r));
  const wait = waitScore(r.daysOld);
  const pending = Math.min(100, pendingOf(r));
  const score = 0.45 * close + 0.35 * wait + 0.2 * pending;
  // No unreviewed work left → not urgent regardless of the other signals.
  return r.unreviewedCount > 0 ? Math.round(score) : 0;
}

function urgencyLevel(score: number): "High" | "Medium" | "Low" {
  if (score >= 66) return "High";
  if (score >= 40) return "Medium";
  return "Low";
}

const URGENCY_STYLE: Record<string, string> = {
  High: "bg-[#FF4D4D]/15 text-[#FF4D4D]",
  Medium: "bg-[#FFA500]/15 text-[#B26A00] dark:text-[#FFA500]",
  Low: "bg-storesight-bg-tint text-storesight-ink-muted dark:bg-storesight-accent/20 dark:text-storesight-accent-light",
};

function waitingColor(days: number | null): string {
  if (days === null) return "text-storesight-ink-muted dark:text-storesight-ink-muted-dark";
  if (days >= 21) return "text-[#FF4D4D]";
  if (days >= 14) return "text-[#B26A00] dark:text-[#FFA500]";
  return "text-storesight-ink dark:text-storesight-ink-dark";
}

function closesColor(days: number | null): string {
  if (days === null) return "text-storesight-ink-muted dark:text-storesight-ink-muted-dark";
  if (days <= 7) return "text-[#FF4D4D]";
  if (days <= 21) return "text-[#B26A00] dark:text-[#FFA500]";
  return "text-storesight-ink dark:text-storesight-ink-dark";
}

function closesLabel(days: number | null): string {
  if (days === null) return "—";
  if (days < 0) return `overdue ${Math.abs(days)}d`;
  if (days === 0) return "today";
  return `in ${days}d`;
}

function buildResponseSearchUrl(jobId: string): string {
  const params = new URLSearchParams({ job_id: jobId, resp_status: "N" });
  return `https://prod.fieldagent.net/admin/fieldagent/responseSearch/?${params.toString()}`;
}

function buildCollectionReviewUrl(jobId: string, projectId: string): string {
  // Scope to the exact job only — including the project param makes Collection
  // Review pull the whole project (every JID), causing reviewer overlap.
  const params = jobId
    ? new URLSearchParams({ job: jobId })
    : new URLSearchParams({ project: projectId });
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

function StatCard({ label, value, danger }: { label: string; value: number | string; danger?: boolean }) {
  return (
    <div className="rounded-lg border border-storesight-border bg-white px-4 py-3 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
      <div className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">{label}</div>
      <div className={`mt-0.5 text-2xl font-semibold tabular-nums ${danger ? "text-[#FF4D4D]" : "text-storesight-ink dark:text-storesight-ink-dark"}`}>
        {value}
      </div>
    </div>
  );
}

export default function AgedJobsPage() {
  const [rows, setRows] = useState<AgedRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [agesLoading, setAgesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minDays, setMinDays] = useState(0);
  const [sortMode, setSortMode] = useState<SortMode>("urgency");
  const [copied, setCopied] = useState(false);
  const router = useRouter();

  const load = useCallback(async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const jobs = await getBloomJobs(force);
      const aged: AgedRow[] = jobs
        .filter((r) => Number(r.extras?.old_sub ?? 0) > 0)
        .map((r) => ({ ...r, daysOld: null, oldestSubDate: null }));
      setRows(aged);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll submission ages separately — server builds cache at 1 job/sec in background
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const applyAges = (ages: Record<string, string>) => {
      setRows((prev) =>
        prev.map((r) => {
          const iso = (r.jobId ? ages[r.jobId] : undefined) ?? (r.id ? ages[r.id] : undefined) ?? null;
          return iso ? { ...r, oldestSubDate: iso, daysOld: isoToDaysAgo(iso) } : r;
        })
      );
    };

    const poll = async () => {
      try {
        const result = await getSubmissionAges();
        if (!cancelled) {
          applyAges(result.data);
          if (result.loading) {
            setAgesLoading(true);
            timer = setTimeout(poll, 5000);
          } else {
            setAgesLoading(false);
          }
        }
      } catch {
        if (!cancelled) setAgesLoading(false);
      }
    };

    setAgesLoading(true);
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  useEffect(() => { void load(); }, [load]);

  const filtered = [...rows]
    .filter((r) => minDays === 0 || (r.daysOld !== null && r.daysOld >= minDays))
    .sort((a, b) => {
      if (sortMode === "closing") {
        const ac = daysUntilClose(a);
        const bc = daysUntilClose(b);
        // Soonest close first; unknown dates sink to the bottom.
        return (ac ?? Infinity) - (bc ?? Infinity);
      }
      if (sortMode === "waiting") {
        return (b.daysOld ?? -1) - (a.daysOld ?? -1);
      }
      if (sortMode === "backlog") {
        if (b.unreviewedCount !== a.unreviewedCount) return b.unreviewedCount - a.unreviewedCount;
        return pendingOf(b) - pendingOf(a);
      }
      return urgencyScore(b) - urgencyScore(a);
    });

  const closingWithBacklog = rows.filter((r) => {
    const c = daysUntilClose(r);
    return c !== null && c <= 7 && r.unreviewedCount > 0;
  }).length;
  const waiting14 = rows.filter((r) => r.daysOld !== null && r.daysOld >= 14).length;
  const totalUnreviewed = rows.reduce((s, r) => s + (r.unreviewedCount || 0), 0);

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
            Old Submissions
          </h1>
          <p className="mt-0.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {loading
              ? "Loading…"
              : agesLoading
              ? `${filtered.length} job${filtered.length !== 1 ? "s" : ""} · loading submission ages…`
              : `${filtered.length} job${filtered.length !== 1 ? "s" : ""} with old submissions, ranked by urgency`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => load(true)}
            disabled={loading || agesLoading}
            className="rounded-lg border border-storesight-border bg-white px-3 py-2 text-sm font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
          <button
            type="button"
            onClick={() => router.push("/assignments?aged=1")}
            disabled={loading || rows.length === 0}
            className="rounded-lg border border-storesight-accent bg-storesight-accent/10 px-3 py-2 text-sm font-semibold text-storesight-primary transition hover:bg-storesight-accent/20 disabled:opacity-50 dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
          >
            Assign old jobs →
          </button>
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {error}
        </div>
      )}

      <div className="mb-5 grid grid-cols-3 gap-3">
        <StatCard label="Closing ≤7d with backlog" value={closingWithBacklog} danger={closingWithBacklog > 0} />
        <StatCard label="Waiting ≥14 days" value={waiting14} />
        <StatCard label="Unreviewed (aged)" value={totalUnreviewed} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-lg border border-storesight-border bg-white p-1 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
          {SORTS.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setSortMode(s.key)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition ${
                sortMode === s.key
                  ? "bg-storesight-accent/20 text-storesight-primary dark:text-storesight-accent-light"
                  : "text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
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
          disabled={filtered.length === 0}
          onClick={() => {
            const ids = filtered.map((r) => r.jobId ?? r.id ?? "").filter(Boolean).join("\n");
            navigator.clipboard.writeText(ids).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            });
          }}
          className="inline-flex items-center gap-1.5 rounded-lg border border-storesight-border bg-white px-3 py-1.5 text-xs font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-40 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
        >
          {copied ? "Copied!" : `Copy ${filtered.length} job ID${filtered.length !== 1 ? "s" : ""}`}
        </button>
        {agesLoading && (
          <span className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark animate-pulse">
            Fetching submission dates…
          </span>
        )}
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
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Waiting</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Closes</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Unrev.</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Pending</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Urgency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-storesight-border dark:divide-storesight-border-dark">
              {filtered.map((r) => {
                const meta = priorityMeta(r.priority);
                const dClose = daysUntilClose(r);
                const pending = pendingOf(r);
                const level = urgencyLevel(urgencyScore(r));
                return (
                  <tr
                    key={`${r.id}-${r.projectId}`}
                    className="bg-white transition hover:bg-storesight-bg-tint dark:bg-storesight-surface-dark dark:hover:bg-storesight-surface-raised-dark"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className={`inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold ${meta.tint} ${meta.text}`}>
                          {meta.label}
                        </span>
                        {r.jobId && r.projectId ? (
                          <a
                            href={buildCollectionReviewUrl(r.jobId, r.projectId)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="truncate font-medium text-storesight-ink hover:text-storesight-primary dark:text-storesight-ink-dark dark:hover:text-storesight-accent-light"
                          >
                            {r.name || `Job ${r.jobId}`}
                          </a>
                        ) : (
                          <div className="truncate font-medium text-storesight-ink dark:text-storesight-ink-dark">
                            {r.name || `Job ${r.jobId}`}
                          </div>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        {r.projectId && <span>Project {r.projectId}</span>}
                        {r.jobId && r.projectId && (
                          <a
                            href={buildResponseSearchUrl(r.jobId)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:text-storesight-primary dark:hover:text-storesight-accent-light"
                          >
                            · Responses
                          </a>
                        )}
                      </div>
                    </td>
                    <td className={`px-4 py-3 font-semibold tabular-nums ${waitingColor(r.daysOld)}`}>
                      {r.daysOld === null ? (
                        agesLoading ? (
                          <span className="inline-block h-3 w-10 animate-pulse rounded bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark" />
                        ) : (
                          "—"
                        )
                      ) : (
                        <>
                          {r.daysOld === 0 ? "<1d" : `${r.daysOld}d`}
                          {r.oldestSubDate && (
                            <div className="text-[10px] font-normal text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                              since {formatDate(r.oldestSubDate)}
                            </div>
                          )}
                        </>
                      )}
                    </td>
                    <td className={`px-4 py-3 tabular-nums ${closesColor(dClose)}`}>
                      {closesLabel(dClose)}
                    </td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums text-storesight-ink dark:text-storesight-ink-dark">
                      {r.unreviewedCount}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark">
                          <div className="h-full rounded-full bg-[#FFA500]" style={{ width: `${Math.min(100, pending)}%` }} />
                        </div>
                        <span className="text-[11px] tabular-nums text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                          {pending}%
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ${URGENCY_STYLE[level]}`}>
                        {level}
                      </span>
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
