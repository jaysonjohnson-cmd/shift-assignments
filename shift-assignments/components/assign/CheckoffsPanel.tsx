"use client";

import { useEffect, useMemo, useState } from "react";
import { getCheckoffs, type Checkoff } from "@/lib/api";
import { formatRelative } from "@/lib/relativeTime";
import { reviewerColor } from "@/lib/types";

function formatClock(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  // The QC team is in Arkansas; shift times are US Central everywhere else.
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Chicago",
  });
}

/**
 * Every checkmark on the current shift, newest first: who checked off which
 * job, when, and whether they went past the unreviewed-responses warning.
 * "Done for the shift" close-outs aren't checkmarks and aren't listed.
 */
export function CheckoffsPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [items, setItems] = useState<Checkoff[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [anywayOnly, setAnywayOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getCheckoffs()
      .then((c) => {
        if (!cancelled) setItems(c);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load check-offs");
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const reviewers = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of items ?? []) m.set(c.reviewerEmail, c.reviewerName || c.reviewerEmail);
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [items]);

  const visible = useMemo(
    () =>
      (items ?? []).filter(
        (c) => (!reviewer || c.reviewerEmail === reviewer) && (!anywayOnly || c.overridden),
      ),
    [items, reviewer, anywayOnly],
  );
  const anywayCount = (items ?? []).filter((c) => c.overridden).length;

  if (error) {
    return (
      <div className="rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          aria-label="Filter by reviewer"
          className="rounded-lg border border-storesight-border bg-white px-3 py-1.5 text-xs font-medium text-storesight-ink dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
        >
          <option value="">All reviewers</option>
          {reviewers.map(([email, name]) => (
            <option key={email} value={email}>
              {name}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setAnywayOnly(!anywayOnly)}
          className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
            anywayOnly
              ? "border-storesight-accent bg-storesight-accent/15 text-storesight-primary dark:border-storesight-accent-light dark:text-storesight-accent-light"
              : "border-storesight-border text-storesight-ink-muted hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light"
          }`}
        >
          <input type="checkbox" checked={anywayOnly} readOnly className="cursor-pointer" />
          Marked done anyway only
          <span className="tabular-nums opacity-70">({anywayCount})</span>
        </button>
        <span className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          {items === null ? "Loading…" : `${visible.length} check-off${visible.length === 1 ? "" : "s"}`}
        </span>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-storesight-border bg-white dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-storesight-border text-[11px] uppercase tracking-wide text-storesight-ink-muted dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark">
              <th className="px-4 py-2.5 text-left font-medium">When</th>
              <th className="px-3 py-2.5 text-left font-medium">Reviewer</th>
              <th className="px-3 py-2.5 text-left font-medium">Job</th>
              <th className="px-3 py-2.5 text-right font-medium">Unreviewed now</th>
              <th className="px-4 py-2.5 text-left font-medium">How</th>
            </tr>
          </thead>
          <tbody>
            {items !== null && visible.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  No check-offs yet this shift
                </td>
              </tr>
            ) : (
              visible.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-storesight-border/60 align-top last:border-0 dark:border-storesight-border-dark/60"
                >
                  <td className="whitespace-nowrap px-4 py-3">
                    <div className="tabular-nums text-storesight-ink dark:text-storesight-ink-dark">
                      {formatClock(c.completedAt)}
                    </div>
                    <div className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {formatRelative(c.completedAt)}
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2">
                      <span
                        aria-hidden
                        className="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{
                          background: reviewerColor({
                            color: c.reviewerColor ?? undefined,
                            email: c.reviewerEmail,
                          }),
                        }}
                      />
                      <span className="truncate font-medium text-storesight-ink dark:text-storesight-ink-dark">
                        {c.reviewerName || c.reviewerEmail}
                      </span>
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <div className="text-storesight-ink dark:text-storesight-ink-dark">
                      {c.jobName || `Job ${c.jobId}`}
                    </div>
                    <div className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark tabular-nums">
                      {c.projectId && <>PID {c.projectId} · </>}JID {c.jobId}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-right tabular-nums text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                    {c.unreviewedNow ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1.5">
                      {c.overridden ? (
                        <span className="rounded-full bg-[#FFA500]/15 px-2 py-0.5 text-[11px] font-medium text-[#FFA500]">
                          Marked done anyway
                        </span>
                      ) : (
                        <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                          Checkmark
                        </span>
                      )}
                      {c.superseded && (
                        <span
                          title="New responses came in and the job went back on their list"
                          className="rounded-full bg-storesight-bg-tint px-2 py-0.5 text-[11px] font-medium text-storesight-ink-muted dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
                        >
                          Reopened
                        </span>
                      )}
                    </div>
                    {c.note && (
                      <div className="mt-1 text-[11px] italic text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        “{c.note}”
                      </div>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
