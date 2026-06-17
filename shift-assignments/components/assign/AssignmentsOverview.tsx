"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getShiftOverview, type ShiftOverview, getShiftJobs, type ShiftJobs, type ShiftJob, clearShift } from "@/lib/api";
import { formatRelative } from "@/lib/relativeTime";

export function AssignmentsOverview({ onBack }: { onBack: () => void }) {
  const [data, setData] = useState<ShiftOverview | null>(null);
  const [jobs, setJobs] = useState<ShiftJobs | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  const [expandedReviewers, setExpandedReviewers] = useState<Set<string>>(new Set());
  const [showCloseModal, setShowCloseModal] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [o, j] = await Promise.all([getShiftOverview(), getShiftJobs()]);
      setData(o);
      setJobs(j);
      setRefreshedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load overview");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totals = (data?.reviewers ?? []).reduce(
    (acc, r) => ({
      total: acc.total + r.total,
      completed: acc.completed + r.completed,
    }),
    { total: 0, completed: 0 },
  );
  const pctOverall = totals.total === 0 ? 0 : Math.round((totals.completed / totals.total) * 100);

  const toggleExpanded = (email: string) => {
    setExpandedReviewers((prev) => {
      const next = new Set(prev);
      if (next.has(email)) {
        next.delete(email);
      } else {
        next.add(email);
      }
      return next;
    });
  };

  const handleCloseAssignment = async () => {
    setShowCloseModal(false);
    setBusy(true);
    setError(null);
    try {
      await clearShift("all");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to close assignment");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <button
            type="button"
            onClick={onBack}
            className="text-xs font-medium text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:text-storesight-accent-light"
          >
            ← Back to menu
          </button>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
            Current Assignments
          </h1>
          <p className="mt-0.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {data?.snapshot_id
              ? `${totals.completed} of ${totals.total} tasks complete · ${pctOverall}%`
              : "No shift published yet."}
            {data?.published_at && <> · published {formatRelative(data.published_at)}</>}
            {refreshedAt && <> · updated {formatRelative(new Date(refreshedAt))}</>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading || busy}
            className="rounded-lg border border-storesight-border bg-white px-3 py-2 text-sm font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <button
            type="button"
            onClick={() => setShowCloseModal(true)}
            disabled={busy}
            className="rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-3 py-2 text-sm font-medium text-storesight-hot-pink hover:border-storesight-hot-pink/60 hover:bg-storesight-hot-pink/20 transition disabled:opacity-50 dark:border-storesight-hot-pink/40 dark:bg-storesight-hot-pink/10 dark:text-storesight-hot-pink"
            title="Close your assignment and clear all jobs"
          >
            Close my assignment
          </button>
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {error}
        </div>
      )}

      {!loading && data && data.reviewers.length === 0 && (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white/60 px-6 py-10 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-dark/60 dark:text-storesight-ink-muted-dark">
          No reviewer has assignments in the current shift.{" "}
          <Link
            href="/assignments"
            className="text-storesight-primary hover:underline dark:text-storesight-accent-light"
          >
            Publish one first.
          </Link>
        </div>
      )}

      {data && data.reviewers.length > 0 && (
        <>
          <OverallBar completed={totals.completed} total={totals.total} />
          <div className="mt-5 space-y-3">
            {data.reviewers.map((r) => {
              const reviewerJobs = jobs?.jobs_by_reviewer.find((rj) => rj.email.toLowerCase() === r.email.toLowerCase());
              const isExpanded = expandedReviewers.has(r.email);
              return (
                <div key={r.email}>
                  <button
                    type="button"
                    onClick={() => toggleExpanded(r.email)}
                    className="w-full"
                  >
                    <ReviewerCard r={r} isExpanded={isExpanded} jobCount={reviewerJobs?.jobs.length || 0} />
                  </button>
                  {isExpanded && reviewerJobs && reviewerJobs.jobs.length > 0 && (
                    <div className="mt-2 rounded-lg border border-storesight-border bg-storesight-surface-dark/50 dark:border-storesight-border-dark overflow-hidden">
                      <div className="divide-y divide-storesight-border dark:divide-storesight-border-dark">
                        {reviewerJobs.jobs.map((job) => {
                          const meta = getPriorityMeta(job.priority);
                          return (
                            <div
                              key={`${r.email}-${job.id}`}
                              className={`px-4 py-3 flex items-center justify-between gap-4 text-sm transition ${
                                job.completed
                                  ? "bg-emerald-50/30 dark:bg-emerald-400/5"
                                  : "hover:bg-storesight-bg-tint dark:hover:bg-storesight-surface-raised-dark"
                              }`}
                            >
                              <div className="flex items-center gap-3 min-w-0 flex-1">
                                <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold shrink-0 ${meta.tint} ${meta.text}`}>
                                  {meta.label}
                                </span>
                                <div className="min-w-0 flex-1">
                                  <div className="truncate text-storesight-ink-dark dark:text-storesight-ink">
                                    {job.name || (job.projectId ? `Project ${job.projectId}` : job.id)}
                                  </div>
                                  <div className="mt-0.5 flex items-center gap-2 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                                    {job.projectId && <span>Proj: {job.projectId}</span>}
                                    {job.jobId && (
                                      <>
                                        <span>·</span>
                                        <a
                                          href={buildJobUrl(job)}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          onClick={(e) => e.stopPropagation()}
                                          className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
                                          title={`Open Job ${job.jobId} in Media Review`}
                                        >
                                          {truncate(job.jobId, 14)}
                                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden>
                                            <path
                                              d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6"
                                              stroke="currentColor"
                                              strokeWidth="1.6"
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                            />
                                          </svg>
                                        </a>
                                      </>
                                    )}
                                  </div>
                                </div>
                              </div>
                              <div className="flex items-center gap-4 shrink-0">
                                <span className="text-right font-medium text-storesight-ink dark:text-storesight-ink-dark tabular-nums">
                                  {job.unreviewedCount}
                                </span>
                                {job.completed ? (
                                  <span className="inline-flex items-center rounded-full bg-emerald-400/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                                    ✓ Done
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center rounded-full bg-storesight-bg-tint px-2 py-0.5 text-[10px] font-semibold text-storesight-primary dark:bg-storesight-accent/25 dark:text-storesight-accent-light">
                                    Pending
                                  </span>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {showCloseModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="mx-4 rounded-2xl border border-storesight-hot-pink/40 bg-storesight-surface-dark p-8 shadow-2xl dark:bg-storesight-surface-dark max-w-md w-full animate-in fade-in zoom-in duration-300">
            <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-full bg-storesight-hot-pink/15">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" className="text-storesight-hot-pink">
                <path
                  d="M18 6L6 18M6 6l12 12"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-storesight-ink dark:text-storesight-ink-dark">
              Close your assignment?
            </h2>
            <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              This will clear all {totals.total} jobs currently assigned in this shift. You'll need to publish a new shift to reassign them.
            </p>
            <div className="mt-6 flex gap-3">
              <button
                type="button"
                onClick={() => setShowCloseModal(false)}
                className="flex-1 rounded-lg border border-storesight-border bg-white px-4 py-2 text-sm font-medium text-storesight-ink hover:border-storesight-accent hover:text-storesight-primary transition dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
              >
                Keep working
              </button>
              <button
                type="button"
                onClick={handleCloseAssignment}
                className="flex-1 rounded-lg border border-storesight-hot-pink/60 bg-storesight-hot-pink/10 px-4 py-2 text-sm font-semibold text-storesight-hot-pink hover:bg-storesight-hot-pink/20 transition"
              >
                Close it
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function getPriorityMeta(priority: number | null) {
  const PRIORITY_META: Record<number, { label: string; tint: string; text: string }> = {
    1: { label: "P1", tint: "bg-[#FF4D4D]/15", text: "text-[#FF4D4D]" },
    2: { label: "P2", tint: "bg-[#FFA500]/15", text: "text-[#FFA500]" },
    3: { label: "P3", tint: "bg-[#3B82F6]/15", text: "text-[#3B82F6]" },
  };
  if (!priority || priority < 1 || priority > 3) {
    return { label: `P${priority ?? "?"}`, tint: "bg-storesight-bg-tint", text: "text-storesight-primary" };
  }
  return PRIORITY_META[priority];
}

function truncate(s: string | undefined, n: number): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function buildJobUrl(row: ShiftJob): string {
  const MEDIA_REVIEW_URL =
    "https://my.fieldagent.net/admin/fieldagent/media-review-v3/";
  if (!row.jobId) return MEDIA_REVIEW_URL;
  const params = new URLSearchParams({ job: row.jobId });
  if (row.projectId) params.set("project", row.projectId);
  return `${MEDIA_REVIEW_URL}?${params.toString()}#/`;
}

function OverallBar({ completed, total }: { completed: number; total: number }) {
  const pct = total === 0 ? 0 : (completed / total) * 100;
  return (
    <div className="rounded-2xl border border-storesight-border bg-white p-5 dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
      <div className="flex items-baseline justify-between">
        <div className="text-sm font-semibold text-storesight-ink dark:text-storesight-ink-dark">
          Shift progress
        </div>
        <div className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark tabular-nums">
          {completed} / {total} · {Math.round(pct)}%
        </div>
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark">
        <div
          className="h-full rounded-full bg-gradient-to-r from-storesight-accent to-storesight-primary transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ReviewerCard({
  r,
  isExpanded,
  jobCount,
}: {
  r: {
    email: string;
    name: string;
    total: number;
    completed: number;
    pending: number;
    first_priority: number | null;
    last_priority: number | null;
  };
  isExpanded: boolean;
  jobCount: number;
}) {
  const pct = r.total === 0 ? 0 : (r.completed / r.total) * 100;
  const done = r.completed === r.total && r.total > 0;
  return (
    <div className="rounded-2xl border border-storesight-border bg-white p-4 transition hover:border-storesight-accent/60 dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:hover:border-storesight-accent-light/60 text-left">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div className="truncate text-sm font-semibold text-storesight-ink dark:text-storesight-ink-dark">
              {r.name || r.email}
            </div>
            {jobCount > 0 && (
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                className={`shrink-0 transition-transform ${isExpanded ? "rotate-90" : ""}`}
                aria-hidden
              >
                <path
                  d="M9 6l6 6-6 6"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="text-storesight-ink-muted dark:text-storesight-ink-muted-dark"
                />
              </svg>
            )}
          </div>
          {r.name && (
            <div className="truncate text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              {r.email}
            </div>
          )}
        </div>
        <span
          className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${
            done
              ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
              : "bg-storesight-bg-tint text-storesight-primary dark:bg-storesight-accent/25 dark:text-storesight-accent-light"
          }`}
        >
          {done ? "Done" : `${Math.round(pct)}%`}
        </span>
      </div>
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            done
              ? "bg-emerald-500"
              : "bg-gradient-to-r from-storesight-accent to-storesight-primary"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-2 flex items-baseline justify-between text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark tabular-nums">
        <span>
          <span className="font-semibold text-storesight-ink dark:text-storesight-ink-dark">
            {r.completed}
          </span>
          {" / "}
          {r.total} complete
        </span>
        <span>
          {r.first_priority != null && r.last_priority != null
            ? `#${r.first_priority}–${r.last_priority}`
            : ""}
        </span>
      </div>
      {r.pending > 0 && (
        <div className="mt-1 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          {r.pending} pending
        </div>
      )}
      {jobCount > 0 && (
        <div className="mt-2 text-[11px] text-storesight-accent dark:text-storesight-accent-light">
          {jobCount} job{jobCount !== 1 ? "s" : ""} • Click to expand
        </div>
      )}
    </div>
  );
}
