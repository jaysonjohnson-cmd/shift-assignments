"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getBloomProjects } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";

type AgedProject = ProjectSummary & { daysOld: number };

const PRESETS = [
  { label: "All", min: 0 },
  { label: "7+ days", min: 7 },
  { label: "14+ days", min: 14 },
  { label: "30+ days", min: 30 },
];

function daysAgo(iso: string): number {
  if (!iso) return 0;
  const ms = Date.now() - new Date(iso).getTime();
  return Math.floor(ms / 86_400_000);
}

function AgeBadge({ days }: { days: number }) {
  let cls = "";
  let label = "";
  if (days >= 30) {
    cls = "bg-[#FF4D4D]/15 text-[#FF4D4D]";
    label = `${days}d`;
  } else if (days >= 14) {
    cls = "bg-[#FFA500]/15 text-[#FFA500]";
    label = `${days}d`;
  } else if (days >= 7) {
    cls = "bg-yellow-400/15 text-yellow-600 dark:text-yellow-400";
    label = `${days}d`;
  } else {
    cls = "bg-storesight-bg-tint text-storesight-ink-muted dark:bg-storesight-accent/20 dark:text-storesight-accent-light";
    label = days === 0 ? "Today" : `${days}d`;
  }
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-bold tabular-nums ${cls}`}>
      {label}
    </span>
  );
}

function buildResponseSearchUrl(projectId: string): string {
  const params = new URLSearchParams({ project_id: projectId, resp_status: "N" });
  return `https://prod.fieldagent.net/admin/fieldagent/responseSearch/?${params.toString()}`;
}

function buildCollectionReviewUrl(projectId: string): string {
  const params = new URLSearchParams({ project: projectId });
  return `https://prod.fieldagent.net/admin/fieldagent/collection-review/?${params.toString()}#/`;
}

export default function AgedJobsPage() {
  const [projects, setProjects] = useState<AgedProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [minDays, setMinDays] = useState(0);
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const raw = await getBloomProjects();
      const aged: AgedProject[] = raw
        .map((p) => ({ ...p, daysOld: daysAgo(p.oldestSubmission) }))
        .filter((p) => p.oldestSubmission);
      setProjects(aged);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load projects");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = projects
    .filter((p) => p.daysOld >= minDays)
    .sort((a, b) => sortDir === "desc" ? b.daysOld - a.daysOld : a.daysOld - b.daysOld);

  const oldest = projects.length > 0 ? Math.max(...projects.map((p) => p.daysOld)) : 0;

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
              : `${filtered.length} project${filtered.length !== 1 ? "s" : ""} with unreviewed submissions${oldest > 0 ? ` · oldest is ${oldest} days ago` : ""}`}
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

      {/* Filter + sort bar */}
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
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path d="M12 5v14M5 12l7-7 7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
              className={`transition-transform origin-center ${sortDir === "asc" ? "rotate-180" : ""}`}
            />
          </svg>
          {sortDir === "desc" ? "Oldest first" : "Newest first"}
        </button>
      </div>

      {/* List */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Loading…
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white/60 px-6 py-12 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-dark/60 dark:text-storesight-ink-muted-dark">
          No projects match this filter.
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-storesight-border dark:border-storesight-border-dark">
          <table className="w-full text-sm">
            <thead className="border-b border-storesight-border bg-storesight-bg-tint dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  Project
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  Oldest submission
                </th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  Jobs
                </th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  Links
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-storesight-border dark:divide-storesight-border-dark">
              {filtered.map((p) => (
                <tr
                  key={p.projectId}
                  className="bg-white transition hover:bg-storesight-bg-tint dark:bg-storesight-surface-dark dark:hover:bg-storesight-surface-raised-dark"
                >
                  <td className="px-4 py-3">
                    <div className="font-medium text-storesight-ink dark:text-storesight-ink-dark">
                      {p.projectName || `Project ${p.projectId}`}
                    </div>
                    <div className="mt-0.5 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {p.projectId}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <AgeBadge days={p.daysOld} />
                      <span className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        {p.oldestSubmission
                          ? new Date(p.oldestSubmission).toLocaleDateString(undefined, {
                              month: "short",
                              day: "numeric",
                              year: "numeric",
                            })
                          : "—"}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right font-semibold tabular-nums text-storesight-ink dark:text-storesight-ink-dark">
                    {p.jidCount}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="inline-flex items-center gap-2">
                      <a
                        href={buildCollectionReviewUrl(p.projectId)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
                        title="Open in Collection Review"
                      >
                        Review
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden>
                          <path d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </a>
                      <a
                        href={buildResponseSearchUrl(p.projectId)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
                        title="Open unreviewed responses"
                      >
                        Responses
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden>
                          <path d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </a>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
