"use client";

import { useEffect, useState } from "react";
import { getShiftOverview, getShiftJobs, type ReviewerJobs, type ShiftJob } from "@/lib/api";

type TileProps = {
  /** Controlled expand state. When `onToggle` is provided the tile expands in place. */
  expanded?: boolean;
  onToggle?: () => void;
  /** Legacy click handler (e.g. scroll to detail elsewhere on the page). */
  onClick?: () => void;
  disabled?: boolean;
};

export function ProgressTrackerTile({ expanded = false, onToggle, onClick, disabled = false }: TileProps) {
  const expandable = typeof onToggle === "function";
  const [data, setData] = useState<{ completed: number; total: number; pct: number } | null>(null);
  const [loading, setLoading] = useState(true);

  // Per-reviewer job breakdown, loaded lazily the first time the tile is expanded.
  const [reviewers, setReviewers] = useState<ReviewerJobs[] | null>(null);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);

  // Filter for the expanded job list: show all jobs, only pending, or only done.
  const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "done">("all");

  useEffect(() => {
    const load = async () => {
      try {
        const overview = await getShiftOverview();
        const total = overview.reviewers.reduce((sum, r) => sum + r.total, 0);
        const completed = overview.reviewers.reduce((sum, r) => sum + r.completed, 0);
        const pct = total === 0 ? 0 : Math.round((completed / total) * 100);
        setData({ completed, total, pct });
      } catch {
        setData(null);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  useEffect(() => {
    if (!expandable || !expanded || reviewers !== null || jobsLoading) return;
    const loadJobs = async () => {
      setJobsLoading(true);
      setJobsError(null);
      try {
        const j = await getShiftJobs();
        setReviewers(j.jobs_by_reviewer);
      } catch (e) {
        setJobsError(e instanceof Error ? e.message : "Failed to load team jobs");
      } finally {
        setJobsLoading(false);
      }
    };
    void loadJobs();
  }, [expandable, expanded, reviewers, jobsLoading]);

  return (
    <div className="relative overflow-hidden rounded-2xl border border-storesight-border bg-gradient-to-br from-storesight-sky/40 to-storesight-accent/15 transition hover:border-storesight-accent/60 dark:border-storesight-border-dark dark:from-storesight-accent/20 dark:to-storesight-primary/10">
      {/* Header — click to expand/collapse */}
      <button
        type="button"
        onClick={onToggle ?? onClick}
        disabled={disabled || loading}
        className="group relative w-full p-6 text-left disabled:cursor-not-allowed disabled:opacity-50"
        aria-expanded={expandable ? expanded : undefined}
      >
        <div className="relative">
          <div className="mb-3 flex items-start justify-between">
            {/* Icon */}
            <div className="inline-flex items-center justify-center rounded-lg bg-storesight-accent/20 p-3">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="text-storesight-accent">
                <path
                  d="M4 20V10M10 20V4M16 20v-7M22 20H2"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            {/* Expand chevron */}
            {expandable && (
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                className={`text-storesight-ink-muted transition-transform duration-200 dark:text-storesight-ink-muted-dark ${
                  expanded ? "rotate-180" : ""
                }`}
              >
                <path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </div>

          {/* Title */}
          <h3 className="text-lg font-semibold text-storesight-ink dark:text-storesight-ink-dark">
            Progress Tracker
          </h3>

          {/* Description */}
          <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {loading
              ? "Loading progress…"
              : data
                ? `${data.completed} of ${data.total} complete${
                    expandable ? ` · ${expanded ? "click to collapse" : "click to see who's on what"}` : ""
                  }`
                : "View team progress"}
          </p>

          {/* Progress Preview */}
          {data && !loading && (
            <div className="mt-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs font-medium text-storesight-accent dark:text-storesight-accent-light">
                  {data.pct}%
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-storesight-accent to-storesight-primary transition-all duration-500"
                  style={{ width: `${data.pct}%` }}
                />
              </div>
            </div>
          )}
        </div>
      </button>

      {/* Expanded panel — what each team member is working on */}
      {expandable && expanded && (
        <div className="border-t border-storesight-border px-6 pb-6 pt-4 dark:border-storesight-border-dark">
          {jobsLoading && (
            <p className="text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              Loading team jobs…
            </p>
          )}
          {jobsError && (
            <p className="text-sm text-storesight-hot-pink">{jobsError}</p>
          )}
          {!jobsLoading && !jobsError && reviewers && reviewers.length === 0 && (
            <p className="text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              No jobs assigned in the current shift.
            </p>
          )}
          {!jobsLoading && !jobsError && reviewers && reviewers.length > 0 && (
            <>
              {/* Status filter toggle */}
              <div className="mb-4 inline-flex rounded-lg border border-storesight-border p-0.5 dark:border-storesight-border-dark">
                {(["all", "pending", "done"] as const).map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => setStatusFilter(opt)}
                    className={`rounded-md px-3 py-1 text-xs font-medium capitalize transition ${
                      statusFilter === opt
                        ? "bg-storesight-accent/20 text-storesight-primary dark:bg-storesight-accent/30 dark:text-storesight-accent-light"
                        : "text-storesight-ink-muted hover:text-storesight-ink dark:text-storesight-ink-muted-dark dark:hover:text-storesight-ink-dark"
                    }`}
                  >
                    {opt}
                  </button>
                ))}
              </div>

              <div className="space-y-4">
                {reviewers.map((r) => {
                  const done = r.jobs.filter((j) => j.completed).length;
                  const shownJobs = r.jobs.filter((j) =>
                    statusFilter === "all" ? true : statusFilter === "done" ? j.completed : !j.completed,
                  );
                  return (
                    <div key={r.email}>
                      <div className="mb-1.5 flex items-baseline justify-between gap-2">
                        <span className="truncate text-sm font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                          {r.name || r.email}
                        </span>
                        <span className="shrink-0 text-xs text-storesight-ink-muted tabular-nums dark:text-storesight-ink-muted-dark">
                          {done}/{r.jobs.length} done
                        </span>
                      </div>
                      {shownJobs.length === 0 ? (
                        <p className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                          {r.jobs.length === 0 ? "No jobs." : `No ${statusFilter} jobs.`}
                        </p>
                      ) : (
                        <ul className="divide-y divide-storesight-border rounded-lg border border-storesight-border bg-storesight-surface/50 dark:divide-storesight-border-dark dark:border-storesight-border-dark dark:bg-storesight-surface-dark/50">
                          {shownJobs.map((job) => (
                            <JobRow key={`${r.email}-${job.id}`} job={job} />
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function JobRow({ job }: { job: ShiftJob }) {
  const meta = getPriorityMeta(job.priority);
  return (
    <li
      className={`flex items-center justify-between gap-3 px-3 py-2 text-sm ${
        job.completed ? "bg-emerald-50/30 dark:bg-emerald-400/5" : ""
      }`}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className={`inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold ${meta.tint} ${meta.text}`}>
          {meta.label}
        </span>
        <span className="truncate text-storesight-ink dark:text-storesight-ink-dark">
          {job.name || (job.projectId ? `Project ${job.projectId}` : job.id)}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <span className="text-xs text-storesight-ink-muted tabular-nums dark:text-storesight-ink-muted-dark">
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
    </li>
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
