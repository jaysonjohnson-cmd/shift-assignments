"use client";

import { useEffect, useMemo, useState } from "react";
import { AssignMenu } from "@/components/assign/AssignMenu";
import { ShiftComposer } from "@/components/assign/ShiftComposer";
import {
  AssignSummary,
  summarizeShift,
  type SummaryLine,
} from "@/components/assign/AssignSummary";
import { AssignmentsOverview } from "@/components/assign/AssignmentsOverview";
import { useStore } from "@/lib/store";
import { useUser } from "@/lib/useUser";
import { useReviewerSync } from "@/lib/useReviewerSync";
import { getBloomJobs, publishShift } from "@/lib/api";
import { assignShift, plannedTotal } from "@/lib/assign";
import {
  emptyShiftDraft,
  type ProjectSummary,
  type Reviewer,
  type Row,
  type ShiftDraft,
} from "@/lib/types";

type Mode =
  | { kind: "menu" }
  | { kind: "shift"; draft: ShiftDraft }
  | {
      kind: "summary";
      title: string;
      lines: SummaryLine[];
      overflow: number;
    }
  | { kind: "overview" };

export default function AssignmentsPage() {
  const rows = useStore((s) => s.rows);
  const reviewers = useStore((s) => s.reviewers);
  const setLastPublishedAt = useStore((s) => s.setLastPublishedAt);
  const { role, loading: userLoading } = useUser();
  const [hydrated, setHydrated] = useState(false);
  const [mode, setMode] = useState<Mode>({ kind: "menu" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [prioritizeFilter, setPrioritizeFilter] = useState(false);
  const [balanceByResponses, setBalanceByResponses] = useState(false);
  const [showCloseModal, setShowCloseModal] = useState(false);

  useReviewerSync();

  useEffect(() => {
    setHydrated(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (role !== "admin") return;
    getBloomJobs(false, statusFilter || undefined)
      .then((jobs) => {
        if (!cancelled) setProjects(jobs as unknown as ProjectSummary[]);
      })
      .catch(() => {
        /* job list is a nicety — failures shouldn't block the UI */
      });
    return () => {
      cancelled = true;
    };
  }, [role, rows.length, statusFilter]);

  const isAdmin = role === "admin";

  const priorityPool = useMemo<Row[]>(() => {
    return [...rows].sort((a, b) => b.priority - a.priority);
  }, [rows]);

  const cancel = () => {
    setMode({ kind: "menu" });
    setError(null);
  };

  if (!hydrated || userLoading) {
    return (
      <div className="flex flex-1 items-center justify-center py-20 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
        Loading…
      </div>
    );
  }

  if (mode.kind === "menu") {
    return (
      <AssignMenu
        isAdmin={isAdmin}
        onStart={(m) => {
          if (m.kind === "shift") {
            setMode({ kind: "shift", draft: emptyShiftDraft() });
          } else if (m.kind === "overview") {
            setMode({ kind: "overview" });
          }
        }}
      />
    );
  }

  if (mode.kind === "overview") {
    return <AssignmentsOverview onBack={cancel} />;
  }

  if (mode.kind === "summary") {
    return (
      <AssignSummary
        title={mode.title}
        lines={mode.lines}
        overflow={mode.overflow}
        onBack={cancel}
      />
    );
  }

  const handlePublishShift = async (draft: ShiftDraft) => {
    setBusy(true);
    setError(null);
    try {
      const result = assignShift(priorityPool, draft, prioritizeFilter, balanceByResponses);
      const byEmail = toEmailMap(result.assignments, reviewers);
      const resp = await publishShift(byEmail);
      setLastPublishedAt(resp.published_at);
      const lines = summarizeShift("Shift", result.assignments, reviewers);
      setMode({
        kind: "summary",
        title: "Shift published",
        lines,
        overflow: result.leftover.length,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to publish");
    } finally {
      setBusy(false);
    }
  };

  const handleCloseAssignment = () => {
    setShowCloseModal(false);
    setMode({ kind: "menu" });
    setError(null);
  };

  return (
    <div className="px-6 py-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <button
            type="button"
            onClick={cancel}
            className="text-xs font-medium text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:text-storesight-accent-light"
          >
            ← Back to menu
          </button>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
            Assign Shift
          </h1>
          <p className="mt-0.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {priorityPool.length} task{priorityPool.length === 1 ? "" : "s"} pulled from Bloom
          </p>
          <div className="mt-3 space-y-2">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-storesight-ink dark:text-storesight-ink-dark">
                  Filter by status:
                </label>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="rounded border border-storesight-border bg-white px-2 py-1 text-xs text-storesight-ink dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
                >
                  <option value="">All (Default)</option>
                  <option value="N">Unreviewed (N)</option>
                  <option value="P">In Progress (P)</option>
                </select>
              </div>
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={prioritizeFilter}
                  onChange={(e) => setPrioritizeFilter(e.target.checked)}
                  className="rounded"
                />
                <span className="text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  Then fill with higher priority
                </span>
              </label>
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={balanceByResponses}
                onChange={(e) => setBalanceByResponses(e.target.checked)}
                className="rounded"
              />
              <span className="text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                Balance by response count (not just job count)
              </span>
            </label>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={cancel}
            disabled={busy}
            className="rounded-lg border border-storesight-border bg-white px-3 py-2 text-sm font-medium text-storesight-ink-muted hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
          >
            Cancel
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
          <button
            type="button"
            onClick={() => handlePublishShift(mode.draft)}
            disabled={busy || !canPublishShift(mode.draft)}
            className="rounded-lg border border-storesight-accent bg-storesight-accent/10 px-4 py-2 text-sm font-semibold text-storesight-primary transition hover:bg-storesight-accent/20 disabled:opacity-50 dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
          >
            {busy ? "Publishing…" : "Publish shift"}
          </button>
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {error}
        </div>
      )}

      <ShiftComposer
        draft={mode.draft}
        pool={priorityPool.length}
        poolRows={priorityPool}
        reviewers={reviewers}
        projects={projects}
        pinsForOtherShift={new Set()}
        onChange={(next) => setMode({ kind: "shift", draft: next })}
      />

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
              This will clear all {mode.draft.slots.length > 0 ? mode.draft.slots.reduce((sum, s) => sum + Math.floor(s.count), 0) : 0} jobs currently assigned in this shift. You'll need to create a new shift to reassign them.
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

function canPublishShift(draft: ShiftDraft): boolean {
  if (draft.slots.length === 0) return false;
  if (!draft.slots.some((s) => s.reviewerId && s.count > 0)) return false;
  return plannedTotal(draft) > 0;
}

function toEmailMap(
  byReviewerId: Record<string, Row[]>,
  reviewers: Reviewer[],
): Record<string, Row[]> {
  const byId = new Map(reviewers.map((r) => [r.id, r.email]));
  const out: Record<string, Row[]> = {};
  for (const [reviewerId, rows] of Object.entries(byReviewerId)) {
    const email = byId.get(reviewerId);
    if (!email) continue;
    out[email] = [...(out[email] ?? []), ...rows];
  }
  return out;
}

