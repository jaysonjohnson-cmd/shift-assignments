"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
import { getBloomJobs, getBloomProjects, publishShift, clearShift, getShiftJobs, getSubmissionAges, type ShiftJobs } from "@/lib/api";
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
  const setRows = useStore((s) => s.setRows);
  const reviewers = useStore((s) => s.reviewers);
  const setLastPublishedAt = useStore((s) => s.setLastPublishedAt);
  const { role, loading: userLoading } = useUser();
  const [hydrated, setHydrated] = useState(false);
  const [mode, setMode] = useState<Mode>({ kind: "menu" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showOptions, setShowOptions] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [prioritizeFilter, setPrioritizeFilter] = useState(false);
  const [balanceByResponses, setBalanceByResponses] = useState(false);
  const [prioritizeAged, setPrioritizeAged] = useState(false);
  // True oldest-unreviewed-submission date per jobId, used to rank the aged pool.
  const [agedSubDates, setAgedSubDates] = useState<Record<string, string>>({});
  // "Only assign jobs older than N days" (0 = no threshold, just oldest-first).
  const [agedMinDays, setAgedMinDays] = useState(0);
  const [retailPipelineOnly, setRetailPipelineOnly] = useState(false);
  const [showCloseModal, setShowCloseModal] = useState(false);
  const [liveJobs, setLiveJobs] = useState<ShiftJobs | null>(null);

  useReviewerSync();

  // Auto-open composer with aged filter when coming from the Old Submissions page
  useEffect(() => {
    if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("aged") === "1") {
      setPrioritizeAged(true);
      setShowOptions(true);
      setMode({ kind: "shift", draft: emptyShiftDraft() });
    }
  }, []);

  useEffect(() => {
    setHydrated(true);
  }, []);

  // While aged mode is on, pull the true oldest-unreviewed-submission date per
  // job from the server cache (built in the background at ~1 job/sec). Poll
  // until the cache reports it's done so ranking sharpens as ages arrive.
  useEffect(() => {
    if (!prioritizeAged) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const result = await getSubmissionAges();
        if (cancelled) return;
        setAgedSubDates(result.data);
        if (result.loading) timer = setTimeout(poll, 5000);
      } catch {
        // best-effort — fall back to priority order until ages load
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [prioritizeAged]);

  useEffect(() => {
    let cancelled = false;
    if (role !== "admin" && role !== "lead") return;
    getBloomProjects()
      .then((summaries) => {
        if (!cancelled) setProjects(summaries);
      })
      .catch(() => {
        /* project list is a nicety — failures shouldn't block the UI */
      });
    return () => {
      cancelled = true;
    };
  }, [role, rows.length]);

  const isAdmin = role === "admin" || role === "lead";

  // Pull the live shift's current assignments so we can hide already-assigned
  // jobs from the compose pool — otherwise a newly added reviewer is handed the
  // same top-priority jobs the existing crew holds, and publish strips them as
  // duplicates, leaving the new reviewer with nothing.
  const refreshLiveJobs = useCallback(() => {
    if (role !== "admin" && role !== "lead") return;
    getShiftJobs()
      .then(setLiveJobs)
      .catch(() => {
        /* best-effort — exclusion is a nicety, never block composing */
      });
  }, [role]);

  useEffect(() => {
    if (mode.kind === "shift") refreshLiveJobs();
  }, [mode.kind, refreshLiveJobs]);

  const reviewerEmailById = useMemo(
    () => new Map(reviewers.map((r) => [r.id, r.email.toLowerCase()])),
    [reviewers],
  );

  // Job keys held by live reviewers who are NOT part of the current draft. Those
  // jobs stay with their reviewer on a merge-publish, so they shouldn't be
  // offered again here. A reviewer being (re)assigned in this draft keeps their
  // jobs available so re-cutting them still works.
  const assignedElsewhereKeys = useMemo(() => {
    const keys = new Set<string>();
    if (!liveJobs) return keys;
    const draftEmails = new Set(
      (mode.kind === "shift" ? mode.draft.slots : [])
        .map((s) => reviewerEmailById.get(s.reviewerId))
        .filter((e): e is string => !!e),
    );
    for (const group of liveJobs.jobs_by_reviewer) {
      if (draftEmails.has(group.email.toLowerCase())) continue;
      for (const job of group.jobs) {
        const k = String(job.jobId || job.id || "");
        if (k) keys.add(k);
      }
    }
    return keys;
  }, [liveJobs, mode, reviewerEmailById]);

  const priorityPool = useMemo<Row[]>(() => {
    // Only pull ACTIVE work into the shift — never assign a job that has no
    // unreviewed responses left. Empty jobs in the feed would otherwise land in
    // reviewers' queues as "old" JIDs with nothing to review.
    let filtered = (rows as Row[]).filter((r) => (r.unreviewedCount || 0) > 0);
    if (prioritizeAged) {
      filtered = filtered.filter((r) => Number(r.extras?.old_sub ?? 0) > 0);
    }
    if (retailPipelineOnly) {
      filtered = filtered.filter(
        (r) =>
          String(r.extras?.client ?? "").toLowerCase() ===
          "retailpipeline@fieldagent.net",
      );
    }
    if (assignedElsewhereKeys.size > 0) {
      filtered = filtered.filter(
        (r) => !assignedElsewhereKeys.has(String(r.jobId || r.id || "")),
      );
    }
    // Aged mode: rank by the job's TRUE oldest unreviewed submission (from the
    // submission-ages cache), oldest first — so reviewers get the genuinely
    // oldest backlog, not whatever has the highest priority flag. An optional
    // "older than N days" threshold drops jobs whose oldest submission isn't
    // actually that old yet (unknown ages count as 0 days until they load, so
    // a positive threshold only ever assigns confirmed-old work).
    if (prioritizeAged) {
      const dateOf = (r: Row) =>
        agedSubDates[String(r.jobId || r.id || "")] || "";
      const daysOf = (r: Row) => {
        const iso = dateOf(r);
        if (!iso) return -1; // unknown age
        const t = new Date(iso).getTime();
        return isNaN(t) ? -1 : Math.floor((Date.now() - t) / 86_400_000);
      };
      if (agedMinDays > 0) {
        filtered = filtered.filter((r) => daysOf(r) >= agedMinDays);
      }
      return [...filtered].sort((a, b) => {
        const da = dateOf(a);
        const db = dateOf(b);
        // Known oldest dates first (ascending = oldest), unknowns sink last,
        // priority as the final tiebreak.
        if (da && db) return da < db ? -1 : da > db ? 1 : b.priority - a.priority;
        if (da) return -1;
        if (db) return 1;
        return b.priority - a.priority;
      });
    }
    return [...filtered].sort((a, b) => b.priority - a.priority);
  }, [rows, prioritizeAged, retailPipelineOnly, assignedElsewhereKeys, agedSubDates, agedMinDays]);

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
      const result = assignShift(priorityPool, draft, prioritizeFilter, balanceByResponses, prioritizeAged);
      const byEmail = toEmailMap(result.assignments, reviewers);
      const resp = await publishShift(byEmail);
      setLastPublishedAt(resp.published_at);
      refreshLiveJobs();
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

  const handleCloseAssignment = async () => {
    setShowCloseModal(false);
    setBusy(true);
    setError(null);
    try {
      await clearShift("all");
      setMode({ kind: "menu" });
      // Best-effort Bloom refresh — never block the UI clear
      getBloomJobs(true, "N").then((fetched) =>
        setRows(fetched, `Bloom · ${fetched.length} jobs (unreviewed)`)
      ).catch(() => undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to end shift");
    } finally {
      setBusy(false);
    }
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
            {assignedElsewhereKeys.size > 0 && (
              <> · {assignedElsewhereKeys.size} already assigned in the live shift (hidden)</>
            )}
          </p>
          <div className="mt-3">
            <button
              type="button"
              onClick={() => setShowOptions(!showOptions)}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:text-storesight-accent-light transition"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
                <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" />
                <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h.2a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.2a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
              </svg>
              Options
              {(prioritizeFilter || balanceByResponses || statusFilter || prioritizeAged || retailPipelineOnly) && (
                <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-storesight-primary text-[9px] font-bold text-white dark:bg-storesight-accent-light dark:text-storesight-surface-dark">
                  {[prioritizeFilter, balanceByResponses, !!statusFilter, prioritizeAged, retailPipelineOnly].filter(Boolean).length}
                </span>
              )}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden className={`transition-transform ${showOptions ? "rotate-180" : ""}`}>
                <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            {showOptions && (
              <div className="mt-2 rounded-xl border border-storesight-border bg-white p-3 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark space-y-3">
                <div className="flex items-center gap-2">
                  <label className="text-xs font-medium text-storesight-ink dark:text-storesight-ink-dark">
                    Filter by status:
                  </label>
                  <select
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                    className="rounded border border-storesight-border bg-white px-2 py-1 text-xs text-storesight-ink dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:text-storesight-ink-dark"
                  >
                    <option value="">All (Default)</option>
                    <option value="N">Unreviewed (N)</option>
                    <option value="P">In Progress (P)</option>
                  </select>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setPrioritizeFilter(!prioritizeFilter)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                      prioritizeFilter
                        ? "border border-storesight-primary bg-storesight-primary/10 text-storesight-primary dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
                        : "border border-storesight-border bg-white text-storesight-ink-muted hover:border-storesight-primary/40 dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:text-storesight-ink-muted-dark"
                    }`}
                  >
                    {prioritizeFilter ? "✓ " : ""}Fill with higher priority
                  </button>
                  <button
                    type="button"
                    onClick={() => setBalanceByResponses(!balanceByResponses)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                      balanceByResponses
                        ? "border border-storesight-primary bg-storesight-primary/10 text-storesight-primary dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
                        : "border border-storesight-border bg-white text-storesight-ink-muted hover:border-storesight-primary/40 dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:text-storesight-ink-muted-dark"
                    }`}
                  >
                    {balanceByResponses ? "✓ " : ""}Balance by responses
                  </button>
                  <button
                    type="button"
                    onClick={() => setPrioritizeAged(!prioritizeAged)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                      prioritizeAged
                        ? "border border-storesight-primary bg-storesight-primary/10 text-storesight-primary dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
                        : "border border-storesight-border bg-white text-storesight-ink-muted hover:border-storesight-primary/40 dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:text-storesight-ink-muted-dark"
                    }`}
                  >
                    {prioritizeAged ? "✓ " : ""}Prioritize old submissions
                  </button>
                  {prioritizeAged && (
                    <label
                      title="Only assign jobs whose oldest unreviewed submission is at least this many days old. 0 = no minimum (just oldest-first)."
                      className="inline-flex items-center gap-1.5 rounded-lg border border-storesight-border bg-white px-3 py-1.5 text-xs font-medium text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:text-storesight-ink-muted-dark"
                    >
                      Older than
                      <input
                        type="number"
                        min={0}
                        max={60}
                        value={agedMinDays}
                        onChange={(e) =>
                          setAgedMinDays(Math.max(0, Number(e.target.value) || 0))
                        }
                        className="w-12 rounded border border-storesight-border bg-transparent px-1.5 py-0.5 text-center tabular-nums text-storesight-ink dark:border-storesight-border-dark dark:text-storesight-ink-dark"
                      />
                      days
                    </label>
                  )}
                  <button
                    type="button"
                    onClick={() => setRetailPipelineOnly(!retailPipelineOnly)}
                    title="Only assign Storesight / Retail Pipeline jobs (client retailpipeline@fieldagent.net)"
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                      retailPipelineOnly
                        ? "border border-storesight-primary bg-storesight-primary/10 text-storesight-primary dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
                        : "border border-storesight-border bg-white text-storesight-ink-muted hover:border-storesight-primary/40 dark:border-storesight-border-dark dark:bg-storesight-surface-dark dark:text-storesight-ink-muted-dark"
                    }`}
                  >
                    {retailPipelineOnly ? "✓ " : ""}Storesight / Retail Pipeline only
                  </button>
                </div>
              </div>
            )}
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
            title="End shift and clear all assigned jobs for every reviewer"
          >
            End shift (clear all)
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
              End shift and clear all assignments?
            </h2>
            <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              This will clear all assigned jobs for every reviewer in this shift. You'll need to publish a new shift to reassign them.
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
                End shift
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

