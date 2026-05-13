"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getMyTasks } from "@/lib/api";
import { useUser } from "@/lib/useUser";
import type { Row } from "@/lib/types";
import { OpenInReviewButton } from "@/components/OpenInReviewButton";
import { MarkDoneButton } from "@/components/MarkDoneButton";
import { formatRelative, formatPending } from "@/lib/relativeTime";

type State = {
  loading: boolean;
  error: string | null;
  snapshotId: string | null;
  publishedAt?: string;
  rows: Row[];
};

type Density = "comfortable" | "compact";

const DENSITY_KEY = "storesight-tasks-density";
const VIEW_BY_PID_KEY = "storesight-tasks-view-by-pid";

const EMPTY_QUEUE_MESSAGES = [
  "You've cleared the queue!",
  "Nothing to see here. Great job!",
  "Go home.",
  "Mission accomplished. Go touch grass.",
  "Brain: 404 Not Found. Time to nap!",
  "Queue empty. Recharging sequence initiated...",
  "Boss Battle: submissions. Result: You won.",
  "Blink three times to re-moisturize your eyeballs. You're free!",
  "Quick — close the tab before you get assigned more submissions!",
  "You're done. Why are you still here?",
  "Nothing left. Go haunt a different corner of the internet for a while.",
  "Queue: Empty. Brain: Empty. Desk: Should also be empty. Shoo.",
  "Tasks: Zero. Reasons to be on this page: Also Zero.",
  "Achievement Unlocked: Team Lead.",
  "You are currently viewing a list of zero tasks. This is a very efficient way to waste time.",
  "All done! What will you do now? Coffee? Donut? Nap?",
  "Look at that beautiful empty screen!",
  "All caught up! Your manager might not be watching, but the UI is, and it's very impressed.",
] as const;

function pickRandomMessageIndex(): number {
  return Math.floor(Math.random() * EMPTY_QUEUE_MESSAGES.length);
}

/** Collapse rows sharing the same projectId into a single synthetic "project" row. */
function groupByProject(rows: Row[]): Row[] {
  const buckets = new Map<string, Row[]>();
  for (const r of rows) {
    const key = r.projectId || r.id;
    const list = buckets.get(key);
    if (list) list.push(r);
    else buckets.set(key, [r]);
  }
  const out: Row[] = [];
  for (const [pid, list] of buckets) {
    if (list.length === 1) {
      out.push({ ...list[0], jobId: null, groupIds: [] });
      continue;
    }
    const priority = list.reduce((m, r) => Math.min(m, r.priority), list[0].priority);
    const unreviewedCount = list.reduce((s, r) => s + (r.unreviewedCount || 0), 0);
    const oldestSubmission = list
      .map((r) => r.oldestSubmission)
      .filter(Boolean)
      .sort()[0] ?? "";
    const allDone = list.every((r) => !!r.completedAt);
    const completedAt = allDone
      ? list
          .map((r) => r.completedAt as string)
          .sort()
          .slice(-1)[0]
      : null;
    const named = list.find((r) => r.name && r.name.trim());
    const namedProject = list.find((r) => r.projectName && r.projectName.trim());
    out.push({
      id: pid,
      projectId: pid,
      projectName: namedProject?.projectName || "",
      jobId: null,
      groupIds: [],
      priority,
      name: named?.name || "",
      unreviewedCount,
      oldestSubmission,
      extras: { jobCount: list.length },
      completedAt,
    });
  }
  out.sort((a, b) => a.priority - b.priority);
  return out;
}

const PRIORITY_META: Record<
  number,
  { label: string; color: string; tint: string; text: string }
> = {
  1: {
    label: "P1",
    color: "#FF4D4D",
    tint: "bg-[#FF4D4D]/15",
    text: "text-[#FF4D4D]",
  },
  2: {
    label: "P2",
    color: "#FFA500",
    tint: "bg-[#FFA500]/15",
    text: "text-[#FFA500]",
  },
  3: {
    label: "P3",
    color: "#3B82F6",
    tint: "bg-[#3B82F6]/15",
    text: "text-[#3B82F6]",
  },
};

function priorityMeta(p: number) {
  if (PRIORITY_META[p]) return PRIORITY_META[p];
  return { ...PRIORITY_META[3], label: `P${p}` };
}

function truncate(value: string | null | undefined, max: number): string {
  if (!value) return "";
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function buildJobUrl(row: Row): string {
  const MEDIA_REVIEW_URL = "https://my.fieldagent.net/admin/fieldagent/media-review/";
  const params = new URLSearchParams();
  const pid = row.projectId || row.id;
  if (pid) params.set("project_id", pid);
  if (row.jobId) params.set("job_id", row.jobId);
  if (row.groupIds && row.groupIds.length) {
    params.set("group_ids", row.groupIds.join(","));
  }
  const qs = params.toString();
  return qs ? `${MEDIA_REVIEW_URL}?${qs}` : MEDIA_REVIEW_URL;
}

function primaryHeading(row: Row, grouped: boolean): string {
  // Prefer the enriched job name when we have one; fall back to IDs otherwise.
  // IDs remain visible in the secondary "Project X · Job Y" line, and the
  // clipboard payload (OpenInReviewButton) always copies IDs — never names.
  if (row.name && row.name.trim()) return row.name;
  if (!grouped) {
    if (row.jobId) return `Job ${row.jobId}`;
    return `Project ${row.projectId || row.id}`;
  }
  if (row.projectId) return `Project ${row.projectId}`;
  if (row.jobId) return `Job ${truncate(row.jobId, 12)}`;
  return row.id;
}

export default function MyTasksPage() {
  const { user, role, loading: userLoading } = useUser();
  const [state, setState] = useState<State>({
    loading: true,
    error: null,
    snapshotId: null,
    rows: [],
  });
  const [density, setDensity] = useState<Density>("comfortable");
  const [viewByPid, setViewByPid] = useState(false);
  const [emptyMsgIdx, setEmptyMsgIdx] = useState(() => pickRandomMessageIndex());

  useEffect(() => {
    try {
      const savedDensity = window.localStorage.getItem(DENSITY_KEY);
      if (savedDensity === "compact" || savedDensity === "comfortable") {
        setDensity(savedDensity);
      }
      const savedView = window.localStorage.getItem(VIEW_BY_PID_KEY);
      if (savedView === "1") setViewByPid(true);
    } catch {
      // ignore
    }
  }, []);

  const changeDensity = (next: Density) => {
    setDensity(next);
    try {
      window.localStorage.setItem(DENSITY_KEY, next);
    } catch {
      // ignore
    }
  };

  const changeViewByPid = (next: boolean) => {
    setViewByPid(next);
    try {
      window.localStorage.setItem(VIEW_BY_PID_KEY, next ? "1" : "0");
    } catch {
      // ignore
    }
  };

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    setEmptyMsgIdx(pickRandomMessageIndex());
    try {
      const data = await getMyTasks();
      setState({
        loading: false,
        error: null,
        snapshotId: data.snapshot_id,
        publishedAt: data.published_at,
        rows: data.rows,
      });
    } catch (e) {
      setState((s) => ({
        ...s,
        loading: false,
        error: e instanceof Error ? e.message : "Failed to load tasks",
      }));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  const handleRowChange = (rowId: string, completedAt: string | null) => {
    setState((s) => ({
      ...s,
      rows: s.rows.map((r) =>
        (r.projectId || r.id) === rowId ? { ...r, completedAt } : r,
      ),
    }));
  };

  const visibleRows = viewByPid ? groupByProject(state.rows) : state.rows;
  const todo = visibleRows.filter((r) => !r.completedAt);
  const done = visibleRows.filter((r) => !!r.completedAt);
  const total = todo.length + done.length;
  const pct = total > 0 ? Math.round((done.length / total) * 100) : 0;

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
            My Tasks
          </h1>
          <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {userLoading
              ? "Loading…"
              : `Signed in as ${user?.email ?? "—"} · role: ${role}`}
            {state.publishedAt && (
              <>
                {" · "}shift published {formatRelative(state.publishedAt)}
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {total > 0 && (
            <div className="flex items-center gap-2">
              <div className="h-1.5 w-40 overflow-hidden rounded-full bg-storesight-border dark:bg-storesight-border-dark">
                <div
                  className="h-full rounded-full bg-storesight-accent transition-[width] duration-500 dark:bg-storesight-accent-light"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="text-[11px] font-medium tabular-nums text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                {done.length}/{total}
              </span>
            </div>
          )}
          <ViewByPidToggle value={viewByPid} onChange={changeViewByPid} />
          <DensityToggle density={density} onChange={changeDensity} />
          <button
            type="button"
            onClick={load}
            disabled={state.loading}
            className="rounded-lg border border-storesight-border px-3 py-1.5 text-xs font-medium text-storesight-primary-dark transition hover:border-storesight-accent hover:text-storesight-primary disabled:opacity-50 dark:border-storesight-border-dark dark:text-storesight-ink-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light"
          >
            {state.loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {state.error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {state.error}
        </div>
      )}

      {!state.loading && !state.snapshotId && (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white p-10 text-center dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
          <p className="text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            No shift published yet. Ask an admin to refresh from Bloom and
            publish the shift.
          </p>
          <Link
            href="/assignments"
            className="mt-4 inline-block text-xs font-medium text-storesight-primary hover:underline dark:text-storesight-accent-light"
          >
            Go to Assignments →
          </Link>
        </div>
      )}

      {state.snapshotId && state.rows.length === 0 && !state.loading && (
        <EmptyQueue message={EMPTY_QUEUE_MESSAGES[emptyMsgIdx]} />
      )}

      {total > 0 && todo.length === 0 && (
        <EmptyQueue message={EMPTY_QUEUE_MESSAGES[emptyMsgIdx]} />
      )}

      <div
        key={viewByPid ? "pid" : "job"}
        className="animate-[tasklist-fade_220ms_ease-out]"
      >
        {todo.length > 0 && (
          <Section title="To do" count={todo.length}>
            {todo.map((row) => (
              <TaskCard
                key={row.projectId || row.id}
                row={row}
                density={density}
                grouped={viewByPid}
                onChange={(iso) =>
                  handleRowChange(row.projectId || row.id, iso)
                }
              />
            ))}
          </Section>
        )}

        {done.length > 0 && (
          <Section title="Done today" count={done.length} muted>
            {done.map((row) => (
              <TaskCard
                key={row.projectId || row.id}
                row={row}
                density={density}
                grouped={viewByPid}
                onChange={(iso) =>
                  handleRowChange(row.projectId || row.id, iso)
                }
                completed
              />
            ))}
          </Section>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  count,
  muted,
  children,
}: {
  title: string;
  count: number;
  muted?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex items-baseline gap-2">
        <h2
          className={`text-sm font-semibold uppercase tracking-wide ${
            muted
              ? "text-storesight-ink-muted dark:text-storesight-ink-muted-dark"
              : "text-storesight-primary-dark dark:text-storesight-ink-dark"
          }`}
        >
          {title}
        </h2>
        <span className="rounded-full bg-storesight-bg-tint px-2 py-0.5 text-[10px] font-semibold text-storesight-primary dark:bg-storesight-accent/25 dark:text-storesight-accent-light">
          {count}
        </span>
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function EmptyQueue({ message }: { message: string }) {
  return (
    <div
      key={message}
      className="mt-8 flex min-h-[50vh] items-center justify-center px-4 text-center animate-[tasklist-fade_320ms_ease-out]"
    >
      <h2 className="max-w-3xl bg-gradient-to-r from-storesight-primary via-storesight-accent to-storesight-accent-light bg-clip-text text-lg font-black leading-snug tracking-tight text-transparent sm:text-xl md:text-2xl">
        {message}
      </h2>
    </div>
  );
}

function ViewByPidToggle({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      title={
        value
          ? "Showing one card per Project ID. Click to view each Job."
          : "Showing one card per Job. Click to group by Project ID."
      }
      className={`inline-flex items-center gap-2 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition ${
        value
          ? "border-storesight-accent bg-storesight-accent/15 text-storesight-primary dark:border-storesight-accent-light dark:text-storesight-accent-light"
          : "border-storesight-border text-storesight-ink-muted hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark dark:hover:border-storesight-accent-light dark:hover:text-storesight-accent-light"
      }`}
    >
      <span
        aria-hidden
        className={`relative inline-flex h-3.5 w-7 items-center rounded-full transition-colors ${
          value
            ? "bg-storesight-accent dark:bg-storesight-accent-light"
            : "bg-storesight-border dark:bg-storesight-border-dark"
        }`}
      >
        <span
          className={`absolute h-2.5 w-2.5 rounded-full bg-white shadow transition-transform ${
            value ? "translate-x-3.5" : "translate-x-0.5"
          }`}
        />
      </span>
      View by PID
    </button>
  );
}

function DensityToggle({
  density,
  onChange,
}: {
  density: Density;
  onChange: (next: Density) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Layout density"
      className="inline-flex overflow-hidden rounded-lg border border-storesight-border dark:border-storesight-border-dark"
    >
      <button
        type="button"
        onClick={() => onChange("comfortable")}
        aria-pressed={density === "comfortable"}
        title="Comfortable"
        className={`flex h-7 w-7 items-center justify-center transition ${
          density === "comfortable"
            ? "bg-storesight-accent/20 text-storesight-primary dark:text-storesight-accent-light"
            : "text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark"
        }`}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
          <rect x="4" y="4" width="16" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
          <rect x="4" y="14" width="16" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      </button>
      <button
        type="button"
        onClick={() => onChange("compact")}
        aria-pressed={density === "compact"}
        title="Compact"
        className={`flex h-7 w-7 items-center justify-center transition ${
          density === "compact"
            ? "bg-storesight-accent/20 text-storesight-primary dark:text-storesight-accent-light"
            : "text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark"
        }`}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

function TaskCard({
  row,
  onChange,
  completed,
  density,
  grouped,
}: {
  row: Row;
  onChange: (iso: string | null) => void;
  completed?: boolean;
  density: Density;
  grouped?: boolean;
}) {
  const jobCount = grouped
    ? (typeof row.extras?.jobCount === "number" ? (row.extras.jobCount as number) : 1)
    : 1;
  const [exiting, setExiting] = useState(false);
  const meta = priorityMeta(row.priority);
  const heading = primaryHeading(row, !!grouped);

  const handleChange = (iso: string | null) => {
    if (iso) {
      setExiting(true);
      window.setTimeout(() => {
        setExiting(false);
        onChange(iso);
      }, 280);
    } else {
      onChange(iso);
    }
  };

  const isCompact = density === "compact";

  const base = `group relative overflow-hidden rounded-xl border bg-white transition-all duration-200 dark:bg-storesight-surface-raised-dark ${
    completed
      ? "border-emerald-200 opacity-70 dark:border-emerald-400/30"
      : "border-storesight-border hover:-translate-y-0.5 hover:border-storesight-accent/60 hover:shadow-lg hover:shadow-storesight-accent/10 dark:border-storesight-border-dark dark:hover:border-storesight-accent-light/60 dark:hover:shadow-storesight-accent/20"
  } ${exiting ? "translate-x-6 opacity-0" : ""}`;

  const accentBar = (
    <span
      aria-hidden
      className="absolute left-0 top-0 h-full w-1"
      style={{ backgroundColor: meta.color }}
    />
  );

  if (isCompact) {
    return (
      <div className={`${base} flex items-center gap-3 py-2 pl-4 pr-3`}>
        {accentBar}
        <span
          className={`inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold ${meta.tint} ${meta.text}`}
        >
          {meta.label}
        </span>
        <div
          className={`min-w-0 flex-1 truncate text-sm font-medium text-storesight-primary-dark dark:text-storesight-ink-dark ${
            completed ? "line-through" : ""
          }`}
        >
          {heading}
          <span className="ml-2 text-[11px] font-normal text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {row.projectId ? `· ${row.projectId}` : ""}
            {row.jobId ? ` · ${truncate(row.jobId, 10)}` : ""}
          </span>
        </div>
        <div className="hidden shrink-0 items-center gap-2 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark sm:flex">
          <span>
            {row.unreviewedCount}
            {grouped && jobCount > 1 ? ` · ${jobCount}j` : ""}
          </span>
          {row.jobId && (
            <a
              href={buildJobUrl(row)}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
              title={`Open Job ${row.jobId} in Media Review`}
            >
              {truncate(row.jobId, 10)}
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!completed && <OpenInReviewButton row={row} size="sm" variant="primary" />}
          <MarkDoneButton
            row={row}
            onChange={handleChange}
            size="sm"
            variant="ghost"
          />
        </div>
      </div>
    );
  }

  return (
    <div className={`${base} flex items-start justify-between gap-3 py-3 pl-4 pr-3`}>
      {accentBar}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold ${meta.tint} ${meta.text}`}
          >
            {meta.label}
          </span>
          <div
            className={`min-w-0 truncate text-sm font-semibold text-storesight-primary-dark dark:text-storesight-ink-dark ${
              completed ? "line-through" : ""
            }`}
          >
            {heading}
          </div>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          {row.projectId && <span>Project {row.projectId}</span>}
          {row.jobId && (
            <>
              <span>·</span>
              <a
                href={buildJobUrl(row)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-storesight-primary hover:bg-storesight-accent/10 dark:text-storesight-accent-light dark:hover:bg-storesight-accent/20"
                title={`Open Job ${row.jobId} in Media Review`}
              >
                Job {truncate(row.jobId, 14)}
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="M14 3h7v7M10 14 21 3M19 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </a>
            </>
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          <span>
            {row.unreviewedCount} unreviewed
            {grouped && jobCount > 1 ? ` · ${jobCount} jobs` : ""}
          </span>
        </div>
      </div>
      <div className="flex flex-col items-end gap-1.5">
        {!completed && <OpenInReviewButton row={row} size="sm" variant="primary" />}
        <MarkDoneButton row={row} onChange={handleChange} size="sm" variant="ghost" />
      </div>
    </div>
  );
}
