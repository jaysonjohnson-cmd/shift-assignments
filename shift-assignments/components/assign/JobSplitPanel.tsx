"use client";

import { useMemo, useState } from "react";
import { activeSplitSharers } from "@/lib/assign";
import {
  reviewerColor,
  type Reviewer,
  type ReviewerSlot,
  type Row,
  type ShiftDraft,
} from "@/lib/types";

/** Jobs at or above this many responses are offered by default. */
const DEFAULT_MIN_RESPONSES = 100;
/** Cap on the picker list — the pool can hold hundreds of jobs. */
const MAX_LISTED = 50;

const jobKey = (r: Row) => String(r.jobId || r.id || "");

/**
 * Split one large JID across several of the shift's reviewers. Each sharer
 * gets the job on top of their count; Collection Review keeps them off each
 * other's responses. A split needs two or more reviewers to take effect —
 * until then the job stays in the normal pool.
 */
export function JobSplitPanel({
  draft,
  poolRows,
  reviewers,
  onChange,
}: {
  draft: ShiftDraft;
  poolRows: Row[];
  reviewers: Reviewer[];
  onChange: (splits: ShiftDraft["jobSplits"]) => void;
}) {
  const [query, setQuery] = useState("");
  const [minResponses, setMinResponses] = useState(DEFAULT_MIN_RESPONSES);

  const splits = draft.jobSplits ?? {};

  const reviewerById = useMemo(
    () => new Map(reviewers.map((r) => [r.id, r])),
    [reviewers],
  );

  const shiftReviewers: (ReviewerSlot & { reviewer: Reviewer })[] = useMemo(
    () =>
      draft.slots
        .filter((s): s is ReviewerSlot => Boolean(s.reviewerId))
        .map((s) => ({ ...s, reviewer: reviewerById.get(s.reviewerId)! }))
        .filter((s) => s.reviewer),
    [draft.slots, reviewerById],
  );

  const rowByKey = useMemo(() => {
    const m = new Map<string, Row>();
    for (const r of poolRows) if (jobKey(r) && !m.has(jobKey(r))) m.set(jobKey(r), r);
    return m;
  }, [poolRows]);

  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...rowByKey.values()]
      .filter((r) => !(jobKey(r) in splits))
      .filter((r) =>
        q
          ? jobKey(r).toLowerCase().includes(q) ||
            (r.name || "").toLowerCase().includes(q) ||
            (r.projectId || "").toLowerCase().includes(q)
          : (r.unreviewedCount || 0) >= minResponses,
      )
      .sort((a, b) => (b.unreviewedCount || 0) - (a.unreviewedCount || 0))
      .slice(0, MAX_LISTED);
  }, [rowByKey, splits, query, minResponses]);

  const addSplit = (key: string) => {
    // Start with the first two reviewers on the shift — the common case — so a
    // single click already produces a working split. Toggle to adjust.
    onChange({ ...splits, [key]: shiftReviewers.slice(0, 2).map((s) => s.reviewerId) });
  };

  const removeSplit = (key: string) => {
    const next = { ...splits };
    delete next[key];
    onChange(next);
  };

  const toggleSharer = (key: string, reviewerId: string) => {
    const current = splits[key] ?? [];
    const next = current.includes(reviewerId)
      ? current.filter((id) => id !== reviewerId)
      : [...current, reviewerId];
    onChange({ ...splits, [key]: next });
  };

  const splitKeys = Object.keys(splits);

  return (
    <section className="mt-4 rounded-xl border border-storesight-border bg-white/60 p-4 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark/40">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-storesight-primary-dark dark:text-storesight-ink-dark">
            Split a JID
          </h3>
          <p className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            Share one large job between two or more reviewers. Each gets it on
            top of their count.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label
            className="flex items-center gap-1 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark"
            title="Only list jobs with at least this many unreviewed responses (ignored while searching)"
          >
            Min responses
            <input
              type="number"
              min={0}
              value={minResponses}
              onChange={(e) => setMinResponses(Math.max(0, Number(e.target.value) || 0))}
              className="h-8 w-16 rounded-md border border-storesight-border bg-white px-2 text-xs outline-none focus:border-storesight-accent dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
            />
          </label>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search JID, PID, or name…"
            className="h-8 w-56 rounded-md border border-storesight-border bg-white px-2 text-xs outline-none focus:border-storesight-accent dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
          />
        </div>
      </header>

      {splitKeys.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {splitKeys.map((key) => {
            const row = rowByKey.get(key);
            const active = activeSplitSharers(draft, key);
            const total = row?.unreviewedCount ?? 0;
            return (
              <li
                key={key}
                className="rounded-md border border-storesight-accent/50 bg-storesight-accent/5 px-2 py-1.5 dark:border-storesight-accent-light/40"
              >
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                      {row?.name || `Job ${key}`}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      <span className="rounded bg-storesight-bg-tint px-1 py-px font-mono dark:bg-storesight-surface-dark">
                        {key}
                      </span>
                      {row ? (
                        <>
                          <span>·</span>
                          <span>{total} responses</span>
                          {active.length >= 2 && (
                            <>
                              <span>·</span>
                              <span className="font-medium text-storesight-primary dark:text-storesight-accent-light">
                                ≈{Math.ceil(total / active.length)} each across {active.length}
                              </span>
                            </>
                          )}
                        </>
                      ) : (
                        <span>· no longer in the pool — won&apos;t be assigned</span>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeSplit(key)}
                    className="rounded-md border border-transparent px-1.5 py-0.5 text-xs text-storesight-ink-muted transition hover:border-storesight-hot-pink hover:text-storesight-hot-pink dark:text-storesight-ink-muted-dark"
                    aria-label="Remove split"
                    title="Remove split — the job goes back to the normal pool"
                  >
                    ✕
                  </button>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1">
                  {shiftReviewers.map((s) => {
                    const on = (splits[key] ?? []).includes(s.reviewerId);
                    return (
                      <button
                        key={s.reviewerId}
                        type="button"
                        onClick={() => toggleSharer(key, s.reviewerId)}
                        aria-pressed={on}
                        className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition ${
                          on
                            ? "border-storesight-accent bg-storesight-accent/15 text-storesight-primary dark:border-storesight-accent-light dark:text-storesight-accent-light"
                            : "border-storesight-border text-storesight-ink-muted hover:border-storesight-accent dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark"
                        }`}
                      >
                        <span
                          aria-hidden
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ background: reviewerColor(s.reviewer) }}
                        />
                        {on ? "✓ " : ""}
                        {s.reviewer.name}
                      </button>
                    );
                  })}
                  {active.length < 2 && (
                    <span className="text-[10px] font-medium text-[#B26A00] dark:text-[#FFA500]">
                      Pick at least 2 reviewers on this shift — until then it isn&apos;t split.
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {shiftReviewers.length < 2 ? (
        <p className="mt-3 rounded-md border border-dashed border-storesight-border px-3 py-4 text-center text-[11px] text-storesight-ink-muted dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark">
          Add at least two reviewers to the shift to split a job between them.
        </p>
      ) : candidates.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-storesight-border px-3 py-4 text-center text-[11px] text-storesight-ink-muted dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark">
          {query.trim()
            ? "No jobs match this search."
            : `No jobs with ${minResponses}+ responses. Lower the minimum or search for a JID.`}
        </p>
      ) : (
        <ul className="mt-3 max-h-64 space-y-1.5 overflow-y-auto pr-1">
          {candidates.map((r) => (
            <li
              key={jobKey(r)}
              className="flex items-center gap-2 rounded-md border border-storesight-border bg-white/80 px-2 py-1.5 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark/60"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-semibold text-storesight-ink dark:text-storesight-ink-dark">
                  {r.name || `Job ${jobKey(r)}`}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  <span className="rounded bg-storesight-bg-tint px-1 py-px font-mono dark:bg-storesight-surface-dark">
                    {jobKey(r)}
                  </span>
                  {r.projectId && (
                    <>
                      <span>·</span>
                      <span>PID {r.projectId}</span>
                    </>
                  )}
                  <span>·</span>
                  <span className="font-medium">{r.unreviewedCount || 0} responses</span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => addSplit(jobKey(r))}
                className="rounded-md border border-storesight-accent bg-storesight-accent/10 px-2 py-1 text-[11px] font-medium text-storesight-primary transition hover:bg-storesight-accent/20 dark:border-storesight-accent-light dark:text-storesight-accent-light"
              >
                Split…
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
