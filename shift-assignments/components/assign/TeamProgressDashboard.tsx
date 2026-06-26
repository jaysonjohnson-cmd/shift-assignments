"use client";

import { useEffect, useMemo, useState } from "react";
import { getShiftOverview } from "@/lib/api";
import { formatRelative } from "@/lib/relativeTime";
import { useUser } from "@/lib/useUser";

type ProgressData = {
  snapshot_id: string | null;
  published_at?: string;
  reviewers: Array<{
    email: string;
    name: string;
    total: number;
    completed: number;
    pending: number;
    pct: number;
  }>;
  totalCompleted: number;
  totalJobs: number;
  overallPct: number;
};

export function TeamProgressDashboard({ refreshKey = 0, onDismiss }: { refreshKey?: number; onDismiss?: () => void }) {
  const { role } = useUser();
  const [data, setData] = useState<ProgressData | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const overview = await getShiftOverview();

      // Count only completions that match a currently-assigned job (what each
      // reviewer's `completed` already reflects). Counting raw completion docs
      // double-counts duplicates and stale/orphaned completions, which produced
      // impossible totals like "273 of 178 (153%)".
      const totalCompleted = overview.reviewers.reduce((sum, r) => sum + r.completed, 0);
      const totalJobs = overview.reviewers.reduce((sum, r) => sum + r.total, 0);
      const overallPct = totalJobs === 0 ? 0 : Math.round((totalCompleted / totalJobs) * 100);

      setData({
        snapshot_id: overview.snapshot_id,
        published_at: overview.published_at,
        reviewers: overview.reviewers.map((r) => ({
          email: r.email,
          name: r.name,
          total: r.total,
          completed: r.completed,
          pending: r.pending,
          pct: r.total === 0 ? 0 : Math.round((r.completed / r.total) * 100),
        })),
        totalCompleted,
        totalJobs,
        overallPct,
      });
      setLastRefresh(new Date());
    } catch (e) {
      console.error("Failed to load progress:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (role !== "admin" && role !== "lead") {
      setLoading(false);
      return;
    }
    void load();
    const interval = setInterval(() => load(), 120000); // Refresh every 2 minutes
    return () => clearInterval(interval);
  }, [role, refreshKey]);

  if (role !== "admin" && role !== "lead") {
    return null;
  }

  if (loading || !data) {
    return (
      <div className="rounded-2xl border border-storesight-border bg-white p-6 dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <div className="text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Loading team progress…
        </div>
      </div>
    );
  }

  if (!data.snapshot_id) {
    return (
      <div className="rounded-2xl border border-dashed border-storesight-border bg-white/60 p-6 dark:border-storesight-border-dark dark:bg-storesight-surface-dark/60">
        <p className="text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          No shift published yet. Publish a shift to see team progress.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Overall Progress */}
      <div className="rounded-2xl border border-storesight-border bg-white p-6 dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-storesight-ink dark:text-storesight-ink-dark">
              Today's Progress
            </h2>
            <p className="mt-1 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              {data.published_at && <>Published {formatRelative(data.published_at)} </>}
              {lastRefresh && <>· updated {formatRelative(lastRefresh)}</>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              disabled={loading}
              className="rounded-lg border border-storesight-border bg-white px-3 py-2 text-xs font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
            >
              {loading ? "Refreshing…" : "Refresh"}
            </button>
            {onDismiss && (
              <button
                type="button"
                onClick={onDismiss}
                className="rounded-lg border border-storesight-border bg-white p-2 text-storesight-ink-muted transition hover:border-storesight-hot-pink/60 hover:bg-storesight-hot-pink/10 hover:text-storesight-hot-pink dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
                aria-label="Dismiss"
                title="Dismiss"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* Overall Stats */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-storesight-ink dark:text-storesight-ink-dark">
              {data.totalCompleted} of {data.totalJobs} jobs completed
            </span>
            <span className="text-sm font-bold text-storesight-primary dark:text-storesight-accent-light">
              {data.overallPct}%
            </span>
          </div>
          <div className="h-3 w-full overflow-hidden rounded-full bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark">
            <div
              className="h-full rounded-full bg-gradient-to-r from-storesight-accent to-storesight-primary transition-all duration-500"
              style={{ width: `${data.overallPct}%` }}
            />
          </div>
        </div>
      </div>

      {/* Team Breakdown */}
      <div className="rounded-2xl border border-storesight-border bg-white p-6 dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold text-storesight-ink dark:text-storesight-ink-dark">
            Team Breakdown
          </h3>
        </div>
        <div className="space-y-4">
          {data.reviewers.map((reviewer) => (
            <div key={reviewer.email}>
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div>
                    <div className="text-sm font-medium text-storesight-ink dark:text-storesight-ink-dark">
                      {reviewer.name || reviewer.email}
                    </div>
                    <div className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {reviewer.completed} of {reviewer.total}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-bold text-storesight-primary dark:text-storesight-accent-light">
                    {reviewer.pct}%
                  </div>
                  {reviewer.pending > 0 && (
                    <div className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {reviewer.pending} pending
                    </div>
                  )}
                </div>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-storesight-bg-tint dark:bg-storesight-surface-raised-dark">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    reviewer.pct === 100
                      ? "bg-emerald-500"
                      : "bg-gradient-to-r from-storesight-accent to-storesight-primary"
                  }`}
                  style={{ width: `${reviewer.pct}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Status Pills */}
      <div className="flex flex-wrap gap-2">
        {data.reviewers
          .filter((r) => r.pct === 100)
          .map((r) => (
            <span
              key={r.email}
              className="inline-flex items-center rounded-full bg-emerald-400/15 px-2 py-1 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300"
            >
              ✓ {r.name || r.email.split("@")[0]} done
            </span>
          ))}
      </div>
    </div>
  );
}
