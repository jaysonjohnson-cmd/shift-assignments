"use client";

import { useEffect, useMemo, useState } from "react";
import {
  countPinnedJids,
  effectiveSlotCount,
  evenDistribute,
  evenSplit,
  plannedTotal,
  rebalance,
} from "@/lib/assign";
import {
  MAX_SLOTS_PER_SHIFT,
  accentFor,
  reviewerColor,
  type ProjectSummary,
  type Reviewer,
  type ReviewerSlot,
  type Row,
  type ShiftDraft,
} from "@/lib/types";
import { ReviewerPicker } from "./ReviewerPicker";
import { CountEditor } from "./CountEditor";
import { ProjectAssignmentPanel } from "./ProjectAssignmentPanel";
import { ReviewerMultiSelectModal } from "./ReviewerMultiSelectModal";

export function ShiftComposer({
  draft,
  onChange,
  pool,
  poolRows,
  reviewers,
  projects = [],
  pinsForOtherShift,
}: {
  draft: ShiftDraft;
  onChange: (next: ShiftDraft) => void;
  /** Rows available to this shift (already priority-sorted). */
  pool: number;
  /** The actual row objects — needed to count pinned JIDs accurately. */
  poolRows?: Row[];
  reviewers: Reviewer[];
  projects?: ProjectSummary[];
  pinsForOtherShift?: Set<string>;
}) {
  const [showMultiSelect, setShowMultiSelect] = useState(false);
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  // Keep totalTarget bound to the pool when "Assign All" is on.
  useEffect(() => {
    if (draft.assignAll && draft.totalTarget !== pool) {
      const next: ShiftDraft = {
        ...draft,
        totalTarget: pool,
        slots: evenDistribute(draft.slots, pool),
      };
      onChange(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pool, draft.assignAll]);

  const planned = plannedTotal(draft);
  const overflow = Math.max(0, pool - planned);

  const commit = (patch: Partial<ShiftDraft>) => onChange({ ...draft, ...patch });

  // Count a new reviewer should start with so they match a uniform crew: the
  // existing crew's average, capped by how many tasks are still unassigned.
  const newReviewerShare = (existingTotal: number, existingCount: number) => {
    const avg = existingCount > 0 ? Math.round(existingTotal / existingCount) : 0;
    return Math.max(0, Math.min(avg, pool - existingTotal));
  };

  const addSlot = () => {
    if (draft.slots.length >= MAX_SLOTS_PER_SHIFT) return;
    // Assign-all (total pinned to pool) or the first reviewer: even re-split.
    if (draft.assignAll || draft.slots.length === 0) {
      const nextSlots: ReviewerSlot[] = [
        ...draft.slots,
        { reviewerId: "", count: 0, locked: false },
      ];
      commit({ slots: evenDistribute(nextSlots, draft.totalTarget) });
      return;
    }
    // Manual total: keep the crew's counts, grow the total by the new share.
    const existingTotal = draft.slots.reduce((a, s) => a + Math.max(0, Math.floor(s.count)), 0);
    const share = newReviewerShare(existingTotal, draft.slots.length);
    commit({
      slots: [...draft.slots, { reviewerId: "", count: share, locked: false }],
      totalTarget: existingTotal + share,
    });
  };

  const addMultipleSlots = (reviewerIds: string[]) => {
    const slotsToAdd = Math.min(
      reviewerIds.length,
      MAX_SLOTS_PER_SHIFT - draft.slots.length,
    );
    const newIds = reviewerIds.slice(0, slotsToAdd);
    if (newIds.length === 0) {
      setShowMultiSelect(false);
      return;
    }
    // Assign-all (total pinned to pool) or the first reviewers: even re-split.
    if (draft.assignAll || draft.slots.length === 0) {
      const newSlots: ReviewerSlot[] = newIds.map((id) => ({
        reviewerId: id,
        count: 0,
        locked: false,
      }));
      commit({ slots: evenDistribute([...draft.slots, ...newSlots], draft.totalTarget) });
      setShowMultiSelect(false);
      return;
    }
    // Manual total: keep the existing crew's counts exactly as they are and grow
    // the total. Each new reviewer starts at the crew's average; if the pool
    // can't cover the full shares, split whatever's left across the new ones.
    const existingTotal = draft.slots.reduce((a, s) => a + Math.max(0, Math.floor(s.count)), 0);
    const perShare = draft.slots.length > 0 ? Math.round(existingTotal / draft.slots.length) : 0;
    const availForNew = Math.max(0, pool - existingTotal);
    const desiredForNew = perShare * newIds.length;
    const counts =
      desiredForNew <= availForNew
        ? newIds.map(() => perShare)
        : evenSplit(availForNew, newIds.length);
    const newSlots: ReviewerSlot[] = newIds.map((id, i) => ({
      reviewerId: id,
      count: counts[i],
      locked: false,
    }));
    const nextSlots = [...draft.slots, ...newSlots];
    commit({
      slots: nextSlots,
      totalTarget: existingTotal + counts.reduce((a, c) => a + c, 0),
    });
    setShowMultiSelect(false);
  };

  const clearAllSlots = () => {
    commit({ slots: [], projectPins: {} });
    setShowClearConfirm(false);
  };

  const removeSlot = (idx: number) => {
    const removed = draft.slots[idx]?.reviewerId;
    const nextSlots = draft.slots.filter((_, i) => i !== idx);
    const nextPins = { ...draft.projectPins };
    if (removed) delete nextPins[removed];
    // Removing a reviewer should NOT bump everyone else's counts. Keep the
    // remaining reviewers' counts exactly as they were and just drop the
    // removed reviewer's share from the total (their work falls to overflow).
    // In "assign all" mode the total is pinned to the pool, so the freed tasks
    // show up as overflow instead of being redistributed.
    const remainingTotal = nextSlots.reduce(
      (a, s) => a + Math.max(0, Math.floor(s.count)),
      0,
    );
    commit({
      slots: nextSlots,
      totalTarget: draft.assignAll ? draft.totalTarget : remainingTotal,
      projectPins: nextPins,
    });
  };

  const setReviewer = (idx: number, reviewerId: string) => {
    const previous = draft.slots[idx]?.reviewerId;
    const nextPins = { ...draft.projectPins };
    if (previous && previous !== reviewerId) {
      const carry = nextPins[previous] ?? [];
      delete nextPins[previous];
      if (reviewerId) nextPins[reviewerId] = carry;
    }
    commit({
      slots: draft.slots.map((s, i) =>
        i === idx ? { ...s, reviewerId } : s,
      ),
      projectPins: nextPins,
    });
  };

  const setCount = (idx: number, count: number) => {
    const clamped = Math.min(Math.max(0, count), draft.totalTarget);
    const nextSlots = draft.slots.map((s, i) =>
      i === idx ? { ...s, count: clamped } : s,
    );
    commit({ slots: rebalance(nextSlots, idx, draft.totalTarget) });
  };

  const toggleLock = (idx: number) => {
    commit({
      slots: draft.slots.map((s, i) =>
        i === idx ? { ...s, locked: !s.locked } : s,
      ),
    });
  };

  const setTotalTarget = (raw: number) => {
    const clamped = Math.min(Math.max(0, Math.floor(raw)), pool);
    commit({
      totalTarget: clamped,
      slots: evenDistribute(draft.slots, clamped),
    });
  };

  const toggleAssignAll = () => {
    const nextAll = !draft.assignAll;
    const target = nextAll ? pool : Math.min(draft.totalTarget, pool);
    commit({
      assignAll: nextAll,
      totalTarget: target,
      slots: evenDistribute(draft.slots, target),
    });
  };

  const pickedIds = draft.slots
    .map((s) => s.reviewerId)
    .filter((id): id is string => !!id);

  const reviewerById = useMemo(
    () => new Map(reviewers.map((r) => [r.id, r])),
    [reviewers],
  );

  const projectById = useMemo(
    () => new Map(projects.map((p) => [p.projectId, p])),
    [projects],
  );

  const pinnedJidCountByReviewer = useMemo(() => {
    const out: Record<string, number> = {};
    for (const [reviewerId, pids] of Object.entries(draft.projectPins ?? {})) {
      if (poolRows) {
        out[reviewerId] = countPinnedJids(poolRows, pids);
      } else {
        // Fall back to summing jidCount from the project list.
        out[reviewerId] = pids.reduce(
          (a, pid) => a + (projectById.get(pid)?.jidCount ?? 0),
          0,
        );
      }
    }
    return out;
  }, [draft.projectPins, poolRows, projectById]);

  return (
    <section className="rounded-2xl border border-storesight-border bg-white/90 p-5 shadow-[0_1px_0_0_rgba(78,51,156,0.04),0_6px_24px_-12px_rgba(78,51,156,0.18)] dark:border-storesight-border-dark dark:bg-storesight-surface-dark/80">
      <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-storesight-border pb-3 dark:border-storesight-border-dark">
        <div>
          <h2 className="text-base font-semibold tracking-tight text-storesight-primary-dark dark:text-storesight-ink-dark">
            Shift
          </h2>
          <p className="mt-0.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {pool} task{pool === 1 ? "" : "s"} available · {planned} planned · {overflow} → overflow
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          <input
            type="checkbox"
            checked={draft.assignAll}
            onChange={toggleAssignAll}
            className="h-4 w-4 rounded border-storesight-border accent-storesight-accent"
          />
          Assign all available tasks
        </label>
      </header>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs font-medium text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Total to assign
          <input
            type="number"
            min={0}
            max={pool}
            value={draft.totalTarget || ""}
            placeholder=""
            disabled={draft.assignAll}
            onChange={(e) => setTotalTarget(Number(e.target.value) || 0)}
            className="h-8 w-24 rounded-md border-2 border-storesight-accent/40 bg-white px-2 text-sm text-storesight-ink outline-none transition focus:border-storesight-accent focus:ring-2 focus:ring-storesight-accent/20 disabled:opacity-50 dark:border-storesight-accent/30 dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark dark:focus:border-storesight-accent-light"
          />
        </label>
        <div className="ml-auto flex items-center gap-2">
          {draft.slots.length > 0 && (
            <button
              type="button"
              onClick={() => setShowClearConfirm(true)}
              className="rounded-md border border-storesight-border bg-white px-2.5 py-1.5 text-xs font-medium text-storesight-ink-muted transition hover:border-storesight-hot-pink hover:text-storesight-hot-pink disabled:opacity-40 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
            >
              Clear all
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowMultiSelect(true)}
            disabled={draft.slots.length >= MAX_SLOTS_PER_SHIFT}
            className="rounded-md border border-storesight-border bg-white px-2.5 py-1.5 text-xs font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-40 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
          >
            + Add reviewer ({draft.slots.length}/{MAX_SLOTS_PER_SHIFT})
          </button>
        </div>
      </div>

      {draft.slots.length === 0 ? (
        <p className="mt-4 rounded-lg border border-dashed border-storesight-border px-4 py-6 text-center text-xs text-storesight-ink-muted dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark">
          No reviewers assigned yet. Add up to {MAX_SLOTS_PER_SHIFT} reviewers
          for this shift.
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {draft.slots.map((slot, idx) => {
            const pinnedPids = draft.projectPins[slot.reviewerId] ?? [];
            const pinnedCount = pinnedJidCountByReviewer[slot.reviewerId] ?? 0;
            const displayedCount = effectiveSlotCount(slot, pinnedCount);
            const bumped = pinnedCount > slot.count;
            return (
              <li
                key={idx}
                className="flex flex-col gap-1.5 rounded-lg border border-storesight-border bg-white/60 px-2.5 py-2 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark/60"
              >
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{
                      background: slot.reviewerId
                        ? reviewerColor(reviewerById.get(slot.reviewerId))
                        : accentFor(idx),
                    }}
                  />
                  <ReviewerPicker
                    value={slot.reviewerId}
                    onChange={(id) => setReviewer(idx, id)}
                    reviewers={reviewers}
                    exclude={pickedIds.filter((id) => id !== slot.reviewerId)}
                  />
                  <CountEditor
                    value={displayedCount}
                    max={Math.max(draft.totalTarget, pinnedCount)}
                    onChange={(v) => setCount(idx, Math.max(v, pinnedCount))}
                    disabled={
                      draft.assignAll &&
                      slot.locked === false &&
                      draft.slots.length === 1
                    }
                  />
                  {bumped && (
                    <span
                      className="rounded-full border border-storesight-accent/50 bg-storesight-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-storesight-primary dark:text-storesight-accent-light"
                      title={`Auto-bumped from ${slot.count} to fit ${pinnedCount} pinned JIDs`}
                    >
                      bumped +{pinnedCount - slot.count}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => toggleLock(idx)}
                    className={`rounded-md border px-2 py-1 text-[11px] font-medium transition ${
                      slot.locked
                        ? "border-storesight-accent bg-storesight-accent/10 text-storesight-primary dark:border-storesight-accent-light dark:text-storesight-accent-light"
                        : "border-storesight-border text-storesight-ink-muted hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark"
                    }`}
                    title={slot.locked ? "Locked (won't rebalance)" : "Lock count"}
                  >
                    {slot.locked ? "Locked" : "Lock"}
                  </button>
                  <button
                    type="button"
                    onClick={() => removeSlot(idx)}
                    className="rounded-md border border-transparent px-1.5 py-0.5 text-xs text-storesight-ink-muted transition hover:border-storesight-hot-pink hover:text-storesight-hot-pink dark:text-storesight-ink-muted-dark"
                    aria-label="Remove reviewer"
                  >
                    ✕
                  </button>
                </div>
                {pinnedPids.length > 0 && (
                  <div className="ml-4 flex flex-wrap items-center gap-1 text-[10px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                    <span>Projects:</span>
                    {pinnedPids.map((pid) => {
                      const p = projectById.get(pid);
                      return (
                        <span
                          key={pid}
                          className="inline-flex items-center gap-1 rounded-full border border-storesight-border bg-white px-1.5 py-0.5 text-[10px] dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark"
                        >
                          {p?.projectName || `PID ${pid}`}
                          <button
                            type="button"
                            onClick={() =>
                              commit({
                                projectPins: {
                                  ...draft.projectPins,
                                  [slot.reviewerId]: (
                                    draft.projectPins[slot.reviewerId] ?? []
                                  ).filter((x) => x !== pid),
                                },
                              })
                            }
                            aria-label="Unpin"
                            className="opacity-60 hover:opacity-100"
                          >
                            ✕
                          </button>
                        </span>
                      );
                    })}
                    <span>(+{pinnedCount} JIDs)</span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {projects.length > 0 && (
        <ProjectAssignmentPanel
          draft={draft}
          projects={projects}
          reviewers={reviewers}
          pinsForOtherShift={pinsForOtherShift ?? new Set()}
          onChange={(nextPins) => commit({ projectPins: nextPins })}
        />
      )}

      {showMultiSelect && (
        <ReviewerMultiSelectModal
          reviewers={reviewers}
          exclude={pickedIds}
          maxSelectable={MAX_SLOTS_PER_SHIFT - draft.slots.length}
          onConfirm={addMultipleSlots}
          onCancel={() => setShowMultiSelect(false)}
        />
      )}

      {showClearConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="mx-4 rounded-2xl border border-storesight-hot-pink/40 bg-white p-6 shadow-2xl dark:border-storesight-hot-pink/40 dark:bg-storesight-surface-dark max-w-md w-full animate-in fade-in zoom-in duration-300">
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
              Clear all reviewers?
            </h2>
            <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              This will remove all {draft.slots.length} reviewer{draft.slots.length === 1 ? "" : "s"} and clear all project pins. This action cannot be undone.
            </p>
            <div className="mt-6 flex gap-3">
              <button
                type="button"
                onClick={() => setShowClearConfirm(false)}
                className="flex-1 rounded-lg border border-storesight-border bg-white px-4 py-2 text-sm font-medium text-storesight-ink hover:border-storesight-accent hover:text-storesight-primary transition dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
              >
                Keep them
              </button>
              <button
                type="button"
                onClick={clearAllSlots}
                className="flex-1 rounded-lg border border-storesight-hot-pink/60 bg-storesight-hot-pink/10 px-4 py-2 text-sm font-semibold text-storesight-hot-pink hover:bg-storesight-hot-pink/20 transition"
              >
                Clear all
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
