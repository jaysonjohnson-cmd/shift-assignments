"use client";

import { useMemo, useState } from "react";
import type {
  ProjectSummary,
  Reviewer,
  ReviewerSlot,
  ShiftDraft,
} from "@/lib/types";

/**
 * Per-shift, searchable project picker. Lets the admin multi-select projects
 * and mass-assign them to one of the shift's reviewers. Pinned projects
 * appear as chips under the reviewer's slot (rendered in ShiftComposer) and
 * their JIDs count toward the reviewer's slot target — auto-bumping the
 * count if needed.
 */
export function ProjectAssignmentPanel({
  draft,
  projects,
  reviewers,
  pinsForOtherShift,
  onChange,
}: {
  draft: ShiftDraft;
  projects: ProjectSummary[];
  reviewers: Reviewer[];
  /** ProjectIds already pinned in the *other* shift — hidden here. */
  pinsForOtherShift: Set<string>;
  onChange: (pins: ShiftDraft["projectPins"]) => void;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");

  const pinOwner = useMemo(() => {
    const m = new Map<string, string>();
    for (const [reviewerId, pids] of Object.entries(draft.projectPins ?? {})) {
      for (const pid of pids) m.set(pid, reviewerId);
    }
    return m;
  }, [draft.projectPins]);

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

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = projects
      .filter((p) => !pinsForOtherShift.has(p.projectId))
      .filter((p) => {
        if (!q) return true;
        return (
          p.projectName.toLowerCase().includes(q) ||
          p.projectId.toLowerCase().includes(q)
        );
      });
    const dir = sortDir === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => dir * (a.jidCount - b.jidCount));
  }, [projects, pinsForOtherShift, query, sortDir]);

  const toggleSelect = (pid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  };

  const selectedJidCount = useMemo(() => {
    let n = 0;
    for (const p of visible) if (selected.has(p.projectId)) n += p.jidCount;
    return n;
  }, [visible, selected]);

  const assignSelectedTo = (reviewerId: string) => {
    const nextPins: ShiftDraft["projectPins"] = {};
    for (const [rid, pids] of Object.entries(draft.projectPins ?? {})) {
      nextPins[rid] = pids.filter((pid) => !selected.has(pid));
    }
    const current = new Set(nextPins[reviewerId] ?? []);
    for (const pid of selected) current.add(pid);
    nextPins[reviewerId] = Array.from(current);
    onChange(nextPins);
    setSelected(new Set());
    setPopoverOpen(false);
  };

  const unpin = (pid: string) => {
    const nextPins: ShiftDraft["projectPins"] = {};
    for (const [rid, pids] of Object.entries(draft.projectPins ?? {})) {
      nextPins[rid] = pids.filter((p) => p !== pid);
    }
    onChange(nextPins);
  };

  return (
    <section className="mt-4 rounded-xl border border-storesight-border bg-white/60 p-4 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark/40">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-storesight-primary-dark dark:text-storesight-ink-dark">
            Assign projects
          </h3>
          <p className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            Pinned projects go to the chosen reviewer first; the count
            auto-bumps if their JIDs exceed the slot.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
            title={`Sort by JID count — ${sortDir === "desc" ? "largest first" : "smallest first"} (click to flip)`}
            className="flex h-8 items-center gap-1 rounded-md border border-storesight-border bg-white px-2 text-[11px] font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
          >
            JIDs {sortDir === "desc" ? "↓" : "↑"}
          </button>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search project name or PID…"
            className="h-8 w-56 rounded-md border border-storesight-border bg-white px-2 text-xs outline-none focus:border-storesight-accent dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
          />
        </div>
      </header>

      {visible.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-storesight-border px-3 py-4 text-center text-[11px] text-storesight-ink-muted dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark">
          {projects.length === 0
            ? "No projects loaded yet — refresh Bloom to populate."
            : "No projects match this search."}
        </p>
      ) : (
        <ul className="mt-3 max-h-64 space-y-1.5 overflow-y-auto pr-1">
          {visible.map((p) => {
            const ownerId = pinOwner.get(p.projectId);
            const owner = ownerId ? reviewerById.get(ownerId) : undefined;
            const shiftOwned =
              owner !== undefined &&
              shiftReviewers.some((s) => s.reviewerId === ownerId);
            const isChecked = selected.has(p.projectId);
            const checkboxDisabled = shiftOwned && ownerId !== undefined;
            return (
              <li
                key={p.projectId}
                className="flex items-center gap-2 rounded-md border border-storesight-border bg-white/80 px-2 py-1.5 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark/60"
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-storesight-accent"
                  checked={isChecked}
                  disabled={checkboxDisabled}
                  title={
                    checkboxDisabled && owner
                      ? `Already assigned to ${owner.name}`
                      : undefined
                  }
                  onChange={() => toggleSelect(p.projectId)}
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium text-storesight-ink dark:text-storesight-ink-dark">
                    {p.projectName || `Project ${p.projectId}`}
                  </div>
                  <div className="text-[10px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                    PID {p.projectId} · {p.jidCount} JIDs
                    {p.oldestSubmission
                      ? ` · oldest ${p.oldestSubmission.slice(0, 10)}`
                      : ""}
                  </div>
                </div>
                {shiftOwned && owner && (
                  <span className="flex items-center gap-1 rounded-full border border-storesight-accent/40 bg-storesight-accent/10 px-2 py-0.5 text-[10px] font-medium text-storesight-primary dark:text-storesight-accent-light">
                    → {owner.name}
                    <button
                      type="button"
                      onClick={() => unpin(p.projectId)}
                      className="text-[10px] opacity-70 hover:opacity-100"
                      aria-label="Unpin"
                    >
                      ✕
                    </button>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <footer className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-storesight-border pt-2 text-[11px] text-storesight-ink-muted dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark">
        <div className="flex flex-wrap items-center gap-2">
          <span>
            {selected.size} project{selected.size === 1 ? "" : "s"} selected ·{" "}
            {selectedJidCount} JID{selectedJidCount === 1 ? "" : "s"}
          </span>
          {(() => {
            const selectable = visible.filter((p) => {
              const ownerId = pinOwner.get(p.projectId);
              return !(
                ownerId && shiftReviewers.some((s) => s.reviewerId === ownerId)
              );
            });
            const allSelected =
              selectable.length > 0 &&
              selectable.every((p) => selected.has(p.projectId));
            return (
              <button
                type="button"
                disabled={selectable.length === 0}
                onClick={() => {
                  if (allSelected) {
                    setSelected(new Set());
                  } else {
                    setSelected(
                      new Set(selectable.map((p) => p.projectId)),
                    );
                  }
                }}
                className="rounded-md border border-storesight-border bg-white px-2 py-0.5 text-[11px] font-medium text-storesight-ink-muted transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-40 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
              >
                {allSelected ? "Clear selection" : "Select all"}
              </button>
            );
          })()}
        </div>
        <div className="relative">
          <button
            type="button"
            disabled={selected.size === 0 || shiftReviewers.length === 0}
            onClick={() => setPopoverOpen((v) => !v)}
            className="rounded-md border border-storesight-accent bg-storesight-accent/10 px-2 py-1 text-[11px] font-medium text-storesight-primary transition hover:bg-storesight-accent/20 disabled:opacity-40 dark:border-storesight-accent-light dark:text-storesight-accent-light"
          >
            Assign to reviewer ▾
          </button>
          {popoverOpen && shiftReviewers.length > 0 && (
            <div className="absolute right-0 z-10 mt-1 w-48 rounded-md border border-storesight-border bg-white p-1 shadow-lg dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
              {shiftReviewers.map((s) => (
                <button
                  key={s.reviewerId}
                  type="button"
                  onClick={() => assignSelectedTo(s.reviewerId)}
                  className="block w-full truncate rounded px-2 py-1 text-left text-xs text-storesight-ink hover:bg-storesight-accent/10 dark:text-storesight-ink-dark"
                >
                  {s.reviewer.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </footer>
    </section>
  );
}
