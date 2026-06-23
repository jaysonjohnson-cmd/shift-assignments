"use client";

import { useRef, useState } from "react";
import { useStore } from "@/lib/store";
import { clearShift, getBloomJobs, type ClearMode } from "@/lib/api";
import { formatRelative } from "@/lib/relativeTime";
import { TeamProgressDashboard } from "./TeamProgressDashboard";
import { ProgressTrackerTile } from "./ProgressTrackerTile";

type StartMode = { kind: "shift" } | { kind: "overview" };

const ICON_CLASS = "h-6 w-6";

const SunIcon = (
  <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
    <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.6" />
    <path
      d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
    />
  </svg>
);

const RefreshIcon = (
  <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
    <path
      d="M4 10a8 8 0 0 1 14-4M20 14a8 8 0 0 1-14 4M20 4v6h-6M4 20v-6h6"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const ChartIcon = (
  <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
    <path
      d="M4 20V10M10 20V4M16 20v-7M22 20H2"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const TrashIcon = (
  <svg viewBox="0 0 24 24" fill="none" className={ICON_CLASS} aria-hidden>
    <path
      d="M4 7h16M10 11v6M14 11v6M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

export function AssignMenu({
  isAdmin,
  onStart,
}: {
  isAdmin: boolean;
  onStart: (mode: StartMode) => void;
}) {
  const rows = useStore((s) => s.rows);
  const setRows = useStore((s) => s.setRows);
  const fetchedAt = useStore((s) => s.fetchedAt);
  const lastPublishedAt = useStore((s) => s.lastPublishedAt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickingClear, setPickingClear] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [clearCount, setClearCount] = useState(0);
  const [breakdownDismissed, setBreakdownDismissed] = useState(false);
  const dashboardRef = useRef<HTMLDivElement>(null);

  const handleRefresh = async () => {
    setBusy(true);
    setError(null);
    try {
      const fetched = await getBloomJobs(true, "N");
      setRows(fetched, `Bloom · ${fetched.length} jobs (unreviewed)`);
      setToast(`Loaded ${fetched.length} unreviewed jobs from Bloom`);
      setTimeout(() => setToast(null), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load from Bloom");
    } finally {
      setBusy(false);
    }
  };

  const hasRows = rows.length > 0;

  const handleClear = async (mode: ClearMode) => {
    const label =
      mode === "reset"
        ? "ALL shift history — every snapshot, every task, every completion mark — permanently"
        : mode === "all"
          ? "ALL assigned tasks AND completion marks"
          : mode === "active"
            ? "all unfinished tasks (completed tasks stay visible)"
            : "all completed tasks and their completion marks";
    const confirmMsg =
      mode === "reset"
        ? `⚠️ NUCLEAR RESET\n\nThis will permanently delete ALL shift snapshots, ALL task assignments, and ALL completion history across every reviewer.\n\nThis cannot be undone. Continue?`
        : `Clear ${label} for every reviewer? This cannot be undone.`;
    if (!confirm(confirmMsg)) return;
    setPickingClear(false);
    setBusy(true);
    setError(null);
    try {
      const result = await clearShift(mode);
      const parts: string[] = [];
      if (result.cleared_rows)
        parts.push(`${result.cleared_rows} task${result.cleared_rows === 1 ? "" : "s"}`);
      if (result.cleared_completions)
        parts.push(
          `${result.cleared_completions} completion${result.cleared_completions === 1 ? "" : "s"}`,
        );
      // Force-refresh Bloom so the job pool reflects the cleared state
      const fetched = await getBloomJobs(true, "N");
      setRows(fetched, `Bloom · ${fetched.length} jobs (unreviewed)`);
      setClearCount((n) => n + 1);
      setBreakdownDismissed(false);
      setToast(parts.length ? `Cleared ${parts.join(" + ")} · reloaded ${fetched.length} jobs` : `Reloaded ${fetched.length} jobs`);
      setTimeout(() => setToast(null), 4000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to clear");
    } finally {
      setBusy(false);
    }
  };

  const handleProgressClick = () => {
    dashboardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
          Shift Assignments
        </h1>
        <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          {hasRows ? (
            <>
              <span className="font-medium">{rows.length}</span> Bloom task
              {rows.length === 1 ? "" : "s"} pulled
              {fetchedAt && <> · refreshed {formatRelative(new Date(fetchedAt))}</>}
              {lastPublishedAt && (
                <> · last published {formatRelative(lastPublishedAt)}</>
              )}
            </>
          ) : isAdmin ? (
            "Pull the live prioritized job list from Bloom to begin."
          ) : (
            "Ask an admin to refresh the job list from Bloom and publish today's shift."
          )}
        </p>
        {error && (
          <p className="mt-3 inline-block rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-3 py-1.5 text-xs text-storesight-hot-pink">
            {error}
          </p>
        )}
        {toast && (
          <p className="mt-3 inline-block rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-700 dark:text-emerald-300">
            {toast}
          </p>
        )}
      </header>

      {/* Team Progress Dashboard */}
      <div className="mb-8" ref={dashboardRef}>
        {!breakdownDismissed && (
          <TeamProgressDashboard
            refreshKey={clearCount}
            onDismiss={() => setBreakdownDismissed(true)}
          />
        )}
      </div>

      {isAdmin ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Tile
              title="Assign a Shift"
              description="Choose reviewers, pin projects, and publish the queue."
              icon={SunIcon}
              accent="from-storesight-primary/10 to-storesight-accent/10"
              onClick={() => onStart({ kind: "shift" })}
              disabled={!hasRows || busy}
            />
            <Tile
              title={busy ? "Refreshing…" : "Refresh from Priority Page"}
              description="Pull the latest prioritized job list before assigning."
              icon={RefreshIcon}
              accent="from-emerald-400/15 to-storesight-mint/20"
              onClick={handleRefresh}
              disabled={busy}
            />
            <Tile
              title="View Current Assignments"
              description="Live check-in — who's on shift and how far they've gotten."
              icon={ChartIcon}
              accent="from-storesight-sky/40 to-storesight-sky/15"
              onClick={() => onStart({ kind: "overview" })}
            />
            <Tile
              title="Clear tasks"
              description="Wipe active, completed, or both across every reviewer's queue."
              icon={TrashIcon}
              accent="from-storesight-peach/20 to-storesight-sun/20"
              onClick={() => setPickingClear(true)}
              disabled={busy}
              danger
            />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <ProgressTrackerTile onClick={handleProgressClick} disabled={busy} />
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white/60 px-6 py-10 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-dark/60 dark:text-storesight-ink-muted-dark">
          Reviewers can see their assigned tasks on the <strong>My Tasks</strong> page.
        </div>
      )}

      {pickingClear && (
        <Modal onClose={() => setPickingClear(false)} title="Clear tasks">
          <p className="mt-1 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            Applies to every reviewer&apos;s queue for the current shift.
          </p>
          <div className="mt-4 space-y-2">
            <ClearOption
              label="Clear active tasks only"
              desc="Remove unfinished tasks. Completed tasks and their marks stay."
              onClick={() => handleClear("active")}
            />
            <ClearOption
              label="Clear completed tasks only"
              desc="Remove finished tasks and their completion marks. Active tasks stay."
              onClick={() => handleClear("completed")}
            />
            <ClearOption
              label="Clear everything"
              desc="Wipe all tasks and completion marks. Fresh slate for the next shift."
              onClick={() => handleClear("all")}
              danger
            />
            <div className="mt-3 border-t border-storesight-border pt-3 dark:border-storesight-border-dark">
              <ClearOption
                label="Delete all shift history"
                desc="Nuclear reset — permanently removes every snapshot, task, and completion mark across all time. Cannot be undone."
                onClick={() => handleClear("reset")}
                nuclear
              />
            </div>
          </div>
        </Modal>
      )}

    </div>
  );
}

function Tile({
  title,
  description,
  icon,
  accent,
  onClick,
  disabled,
  danger,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  accent: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`group relative flex h-full flex-col overflow-hidden rounded-2xl border border-storesight-border bg-gradient-to-br ${accent} p-5 text-left transition dark:border-storesight-border-dark ${
        disabled
          ? "cursor-not-allowed opacity-50"
          : "hover:-translate-y-0.5 hover:border-storesight-accent/60 hover:shadow-lg"
      } ${danger ? "hover:border-storesight-hot-pink/70" : ""}`}
    >
      <div
        className={`mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-storesight-surface shadow-sm dark:bg-storesight-surface-raised-dark ${
          danger
            ? "text-storesight-hot-pink"
            : "text-storesight-primary dark:text-storesight-accent-light"
        }`}
      >
        {icon}
      </div>
      <h2
        className={`text-base font-semibold ${
          danger
            ? "text-storesight-hot-pink"
            : "text-storesight-ink dark:text-storesight-ink-dark"
        }`}
      >
        {title}
      </h2>
      <p className="mt-2 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
        {description}
      </p>
    </button>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-storesight-border bg-white p-5 shadow-xl dark:border-storesight-border-dark dark:bg-storesight-surface-dark"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-storesight-ink dark:text-storesight-ink-dark">
          {title}
        </h3>
        {children}
        <button
          type="button"
          onClick={onClose}
          className="mt-4 w-full rounded-md px-3 py-1.5 text-xs text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:text-storesight-accent-light"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function ClearOption({
  label,
  desc,
  onClick,
  danger,
  nuclear,
}: {
  label: string;
  desc: string;
  onClick: () => void;
  danger?: boolean;
  nuclear?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-lg border px-4 py-3 text-left transition ${
        nuclear
          ? "border-red-600/70 bg-red-600/10 hover:bg-red-600/20 dark:border-red-500/60 dark:bg-red-600/10 dark:hover:bg-red-600/20"
          : danger
            ? "border-storesight-hot-pink/60 bg-storesight-hot-pink/5 hover:bg-storesight-hot-pink/15 dark:border-storesight-hot-pink/50"
            : "border-storesight-border bg-white hover:border-storesight-accent dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:hover:border-storesight-accent-light"
      }`}
    >
      <div
        className={`text-sm font-semibold ${
          nuclear
            ? "text-red-600 dark:text-red-400"
            : danger
              ? "text-storesight-hot-pink"
              : "text-storesight-ink dark:text-storesight-ink-dark"
        }`}
      >
        {nuclear && <span className="mr-1.5">⚠️</span>}
        {label}
      </div>
      <div className="mt-0.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
        {desc}
      </div>
    </button>
  );
}
