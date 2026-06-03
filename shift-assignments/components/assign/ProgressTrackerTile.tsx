"use client";

import { useEffect, useState } from "react";
import { getShiftOverview } from "@/lib/api";

type TileProps = {
  onClick: () => void;
  disabled?: boolean;
};

export function ProgressTrackerTile({ onClick, disabled = false }: TileProps) {
  const [data, setData] = useState<{ completed: number; total: number; pct: number } | null>(null);
  const [loading, setLoading] = useState(true);

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

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || loading}
      className="group relative overflow-hidden rounded-2xl border border-storesight-border bg-gradient-to-br from-storesight-sky/40 to-storesight-accent/15 p-6 text-left transition hover:border-storesight-accent/60 disabled:cursor-not-allowed disabled:opacity-50 dark:border-storesight-border-dark dark:from-storesight-accent/20 dark:to-storesight-primary/10"
    >
      {/* Background decoration */}
      <div className="pointer-events-none absolute inset-0 opacity-0 transition group-hover:opacity-10">
        <div className="absolute inset-0 bg-gradient-to-br from-storesight-accent to-storesight-primary" />
      </div>

      <div className="relative">
        {/* Icon */}
        <div className="mb-3 inline-flex items-center justify-center rounded-lg bg-storesight-accent/20 p-3">
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

        {/* Title */}
        <h3 className="text-lg font-semibold text-storesight-ink dark:text-storesight-ink-dark">
          Progress Tracker
        </h3>

        {/* Description */}
        <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          {loading ? "Loading progress…" : data ? `${data.completed} of ${data.total} complete` : "View team progress"}
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
  );
}
