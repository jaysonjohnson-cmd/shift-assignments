"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { getShiftJobs, type ShiftJobs, type ShiftJob, clearShift } from "@/lib/api";
import { formatRelative } from "@/lib/relativeTime";
import { ProgressTrackerTile } from "@/components/assign/ProgressTrackerTile";
import { useUser } from "@/lib/useUser";

const PRIORITY_META: Record<number, { label: string; tint: string; text: string }> = {
  1: { label: "P1", tint: "bg-[#FF4D4D]/15", text: "text-[#FF4D4D]" },
  2: { label: "P2", tint: "bg-[#FFA500]/15", text: "text-[#FFA500]" },
  3: { label: "P3", tint: "bg-[#3B82F6]/15", text: "text-[#3B82F6]" },
};

function getPriorityMeta(priority: number | null) {
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
  const COLLECTION_REVIEW_URL =
    "https://prod.fieldagent.net/admin/fieldagent/collection-review/";
  if (!row.jobId) return COLLECTION_REVIEW_URL;
  const params = new URLSearchParams({ job: row.jobId });
  if (row.projectId) params.set("project", row.projectId);
  return `${COLLECTION_REVIEW_URL}?${params.toString()}#/`;
}

export default function TeamAssignmentsPage() {
  const [data, setData] = useState<ShiftJobs | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"reviewer" | "priority">("reviewer");
  const [filterCompleted, setFilterCompleted] = useState(true);
  const [expandedReviewers, setExpandedReviewers] = useState<Set<string>>(new Set());
  const [showCloseModal, setShowCloseModal] = useState(false);
  const [closing, setClosing] = useState(false);
  const progressRef = useRef<HTMLDivElement>(null);
  const { role } = useUser();
  const isAdmin = role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const jobs = await getShiftJobs();
      setData(jobs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load assignments");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleCloseAssignment = async () => {
    setShowCloseModal(false);
    setClosing(true);
    setError(null);
    try {
      await clearShift("all");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to close assignment");
    } finally {
      setClosing(false);
    }
  };

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

  const handleProgressClick = () => {
    progressRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  useEffect(() => {
    void load();
  }, [load]);

  const allJobs = useMemo(() => {
    const jobs: Array<ShiftJob & { reviewer: string; reviewerName: string }> = [];
    if (!data) return jobs;
    for (const group of data.jobs_by_reviewer) {
      for (const job of group.jobs) {
        if (filterCompleted && job.completed) continue;
        jobs.push({
          ...job,
          reviewer: group.email,
          reviewerName: group.name || group.email,
        });
      }
    }
    return jobs;
  }, [data, filterCompleted]);

  const sorted = useMemo(() => {
    const items = [...allJobs];
    if (sortBy === "priority") {
      items.sort((a, b) => {
        const aPri = a.priority ?? 999;
        const bPri = b.priority ?? 999;
        return aPri - bPri;
      });
    } else {
      items.sort((a, b) => a.reviewerName.localeCompare(b.reviewerName));
    }
    return items;
  }, [allJobs, sortBy]);

  const completedCount = useMemo(
    () => (data?.jobs_by_reviewer ?? []).reduce((sum, r) => sum + r.jobs.filter((j) => j.completed).length, 0),
    [data],
  );
  const totalCount = useMemo(
    () => (data?.jobs_by_reviewer ?? []).reduce((sum, r) => sum + r.jobs.length, 0),
    [data],
  );

  return (
    <div className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">
      <div className="mb-6 flex flex-col gap-6">
        <div ref={progressRef}>
          <ProgressTrackerTile onClick={handleProgressClick} disabled={false} />
        </div>
      </div>

      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            href="/assignments"
            className="text-xs font-medium text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:text-storesight-accent-light"
          >
            ← Back to assignments
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
            Team Assignments
          </h1>
          <p className="mt-0.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {data?.snapshot_id
              ? `${completedCount} of ${totalCount} jobs complete`
              : "No shift published yet."}
            {data?.published_at && <> · published {formatRelative(data.published_at)}</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="rounded-lg border border-storesight-border bg-white px-3 py-2 text-sm font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          {isAdmin && data?.snapshot_id && (
            <button
              type="button"
              onClick={() => setShowCloseModal(true)}
              disabled={closing}
              title="Close the shift and clear all assigned jobs"
              className="rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-3 py-2 text-sm font-medium text-storesight-hot-pink transition hover:border-storesight-hot-pink/60 hover:bg-storesight-hot-pink/20 disabled:opacity-50 dark:border-storesight-hot-pink/40 dark:bg-storesight-hot-pink/10 dark:text-storesight-hot-pink"
            >
              {closing ? "Closing…" : "Close my assignments"}
            </button>
          )}
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {error}
        </div>
      )}

      {!loading && !data?.snapshot_id && (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white/60 px-6 py-10 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-dark/60 dark:text-storesight-ink-muted-dark">
          No shift published yet.{" "}
          <Link
            href="/assignments"
            className="text-storesight-primary hover:underline dark:text-storesight-accent-light"
          >
            Publish one first.
          </Link>
        </div>
      )}

      {data?.snapshot_id && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 rounded-lg border border-storesight-border bg-white p-1 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
              <button
                type="button"
                onClick={() => setSortBy("reviewer")}
                className={`px-3 py-1.5 text-xs font-medium rounded transition ${
                  sortBy === "reviewer"
                    ? "bg-storesight-accent/20 text-storesight-primary dark:text-storesight-accent-light"
                    : "text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark"
                }`}
              >
                By Reviewer
              </button>
              <button
                type="button"
                onClick={() => setSortBy("priority")}
                className={`px-3 py-1.5 text-xs font-medium rounded transition ${
                  sortBy === "priority"
                    ? "bg-storesight-accent/20 text-storesight-primary dark:text-storesight-accent-light"
                    : "text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark"
                }`}
              >
                By Priority
              </button>
            </div>
            <button
              type="button"
              onClick={() => setFilterCompleted(!filterCompleted)}
              className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                filterCompleted
                  ? "border-storesight-accent bg-storesight-accent/15 text-storesight-primary dark:border-storesight-accent-light dark:text-storesight-accent-light"
                  : "border-storesight-border text-storesight-ink-muted hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light"
              }`}
            >
              <input type="checkbox" checked={filterCompleted} readOnly className="cursor-pointer" />
              Hide completed
            </button>
          </div>

          {sortBy === "reviewer" ? (
            <div className="space-y-3">
              {(data?.jobs_by_reviewer ?? []).length === 0 ? (
                <div className="rounded-2xl border border-dashed border-storesight-border bg-white/60 px-6 py-10 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-dark/60 dark:text-storesight-ink-muted-dark">
                  No jobs to display
                </div>
              ) : (
                (data?.jobs_by_reviewer ?? []).map((reviewer) => {
                  const visibleJobs = reviewer.jobs.filter((j) => !filterCompleted || !j.completed);
                  const isExpanded = expandedReviewers.has(reviewer.email);
                  const completed = reviewer.jobs.filter((j) => j.completed).length;
                  const total = reviewer.jobs.length;
                  const pct = total === 0 ? 0 : (completed / total) * 100;
                  const done = completed === total && total > 0;

                  return (
                    <div key={reviewer.email}>
                      <button
                        type="button"
                        onClick={() => toggleExpanded(reviewer.email)}
                        className="w-full text-left"
                      >
                        <div className="rounded-2xl border border-storesight-border bg-white p-4 transition hover:border-storesight-accent/60 dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:hover:border-storesight-accent-light/60">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <div className="truncate text-sm font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                                  {reviewer.name || reviewer.email}
                                </div>
                                {visibleJobs.length > 0 && (
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
                              {reviewer.name && (
                                <div className="truncate text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                                  {reviewer.email}
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
                                {completed}
                              </span>
                              {" / "}
                              {total} complete
                            </span>
                          </div>
                          {visibleJobs.length > 0 && (
                            <div className="mt-2 text-[11px] text-storesight-accent dark:text-storesight-accent-light">
                              {visibleJobs.length} job{visibleJobs.length !== 1 ? "s" : ""} • Click to expand
                            </div>
                          )}
                        </div>
                      </button>
                      {isExpanded && visibleJobs.length > 0 && (
                        <div className="mt-2 rounded-lg border border-storesight-border bg-storesight-surface-dark/50 dark:border-storesight-border-dark overflow-hidden">
                          <div className="divide-y divide-storesight-border dark:divide-storesight-border-dark">
                            {visibleJobs.map((job) => {
                              const meta = getPriorityMeta(job.priority);
                              return (
                                <div
                                  key={`${reviewer.email}-${job.id}`}
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
                                      <div className="truncate text-storesight-ink dark:text-storesight-ink-dark">
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
                                              title={`Open Job ${job.jobId} in Collection Review`}
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
                })
              )}
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-storesight-border dark:border-storesight-border-dark">
              <table className="w-full text-sm">
                <thead className="border-b border-storesight-border bg-storesight-bg-tint dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
                  <tr>
                    <th className="px-4 py-3 text-left font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                      Reviewer
                    </th>
                    <th className="px-4 py-3 text-left font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                      Priority
                    </th>
                    <th className="px-4 py-3 text-left font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                      Project / Job
                    </th>
                    <th className="px-4 py-3 text-left font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                      Unreviewed
                    </th>
                    <th className="px-4 py-3 text-center font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-storesight-border dark:divide-storesight-border-dark">
                  {sorted.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        No jobs to display
                      </td>
                    </tr>
                  ) : (
                    sorted.map((job) => {
                      const meta = getPriorityMeta(job.priority);
                      return (
                        <tr
                          key={`${job.reviewer}-${job.id}`}
                          className={`transition ${
                            job.completed
                              ? "bg-emerald-50/50 dark:bg-emerald-400/5"
                              : "hover:bg-storesight-bg-tint dark:hover:bg-storesight-surface-raised-dark"
                          }`}
                        >
                          <td className="px-4 py-3 font-medium text-storesight-ink dark:text-storesight-ink-dark">
                            {job.reviewerName}
                          </td>
                          <td className="px-4 py-3">
                            <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold ${meta.tint} ${meta.text}`}>
                              {meta.label}
                            </span>
                          </td>
                          <td className="px-4 py-3 max-w-xs">
                            <div className="truncate text-storesight-ink dark:text-storesight-ink-dark">
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
                                    className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
                                    title={`Open Job ${job.jobId} in Collection Review`}
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
                          </td>
                          <td className="px-4 py-3 text-right font-medium text-storesight-ink dark:text-storesight-ink-dark">
                            {job.unreviewedCount}
                          </td>
                          <td className="px-4 py-3 text-center">
                            {job.completed ? (
                              <span className="inline-flex items-center rounded-full bg-emerald-400/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                                ✓ Done
                              </span>
                            ) : (
                              <span className="inline-flex items-center rounded-full bg-storesight-bg-tint px-2 py-0.5 text-[10px] font-semibold text-storesight-primary dark:bg-storesight-accent/25 dark:text-storesight-accent-light">
                                Pending
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {showCloseModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="mx-4 w-full max-w-md rounded-2xl border border-storesight-hot-pink/40 bg-storesight-surface-dark p-8 shadow-2xl animate-in fade-in zoom-in duration-300 dark:bg-storesight-surface-dark">
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
              Close your assignments?
            </h2>
            <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              {`This will clear all ${totalCount} jobs currently assigned in this shift. You'll need to publish a new shift to reassign them.`}
            </p>
            <div className="mt-6 flex gap-3">
              <button
                type="button"
                onClick={() => setShowCloseModal(false)}
                className="flex-1 rounded-lg border border-storesight-border bg-white px-4 py-2 text-sm font-medium text-storesight-ink transition hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
              >
                Keep working
              </button>
              <button
                type="button"
                onClick={handleCloseAssignment}
                className="flex-1 rounded-lg border border-storesight-hot-pink/60 bg-storesight-hot-pink/10 px-4 py-2 text-sm font-semibold text-storesight-hot-pink transition hover:bg-storesight-hot-pink/20"
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
