"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getLeaderboard,
  type Leaderboard,
  type LeaderboardPeriod,
  type LeaderboardReviewer,
} from "@/lib/api";
import { useUser } from "@/lib/useUser";
import { reviewerColor } from "@/lib/types";
import { MedalIcon, Podium, initials } from "@/components/leaderboard/Podium";

function colorFor(r: LeaderboardReviewer): string {
  return reviewerColor({ color: r.color ?? undefined, email: r.email });
}

/** Local "YYYY-MM-DD" for a Date. */
function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function currentMonthKey(): string {
  return isoDay(new Date()).slice(0, 7);
}

/** Shift a "YYYY-MM" key by `delta` months. */
function shiftMonth(key: string, delta: number): string {
  const [y, m] = key.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return isoDay(d).slice(0, 7);
}

function monthName(key: string, opts: Intl.DateTimeFormatOptions = { month: "long", year: "numeric" }): string {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, opts);
}

export default function LeaderboardPage() {
  const { role, loading: userLoading } = useUser();
  const [data, setData] = useState<Leaderboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Weekly tab (resets Monday) or Monthly tab (calendar month, browsable).
  const [period, setPeriod] = useState<LeaderboardPeriod>("week");
  const [month, setMonth] = useState<string>(currentMonthKey);
  // "all" = whole period; a number = a single day within it.
  const [view, setView] = useState<"all" | number>("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getLeaderboard(period === "month" ? { period, month } : {}));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load leaderboard");
    } finally {
      setLoading(false);
    }
  }, [period, month]);

  useEffect(() => {
    if (!userLoading) load();
  }, [userLoading, load]);

  const canView = role === "admin" || role === "lead";

  if (userLoading || (loading && !data)) {
    return (
      <div className="flex flex-1 items-center justify-center py-20 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
        Loading…
      </div>
    );
  }

  if (!canView) {
    return (
      <div className="mx-auto w-full max-w-4xl px-6 py-16 text-center text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
        The leaderboard is available to admins and leads.
        <div className="mt-4">
          <Link href="/" className="text-storesight-primary hover:underline dark:text-storesight-accent-light">
            ← Back home
          </Link>
        </div>
      </div>
    );
  }

  const allReviewers = data?.reviewers ?? [];
  const isMonth = period === "month";
  // Day index of "today" within the shown period, or -1 if outside it.
  const todayIdx = data?.day_keys?.indexOf(isoDay(new Date())) ?? -1;
  const isCurrentMonth = month === currentMonthKey();
  const periodLabel = isMonth ? (isCurrentMonth ? "this month" : `in ${monthName(month)}`) : "this week";
  // Metric for the active view: whole-week total, or a single day's count.
  const metric = (r: LeaderboardReviewer) =>
    view === "all" ? r.total : r.days[view] ?? 0;
  // Responses (volume) for the active view, shown alongside the job count.
  const respMetric = (r: LeaderboardReviewer) =>
    view === "all" ? r.responses : r.resp_days?.[view] ?? 0;
  const dayLabel = (i: number) =>
    isMonth ? `${monthName(month, { month: "short" })} ${data?.day_labels[i] ?? ""}` : (data?.day_labels[i] ?? "");
  const scopeLabel = view === "all" ? periodLabel : dayLabel(view);
  // Re-rank by the active metric. In day view, hide reviewers with nothing that day.
  const reviewers = [...allReviewers]
    .filter((r) => (view === "all" ? true : metric(r) > 0))
    .sort((a, b) => metric(b) - metric(a) || a.name.localeCompare(b.name));
  const top3 = reviewers.slice(0, 3);
  const max = (reviewers[0] ? metric(reviewers[0]) : 0) || 1;
  const maxDay = Math.max(1, ...(data?.totals_by_day ?? [0]));
  const teamTotal =
    view === "all"
      ? data?.team_total ?? 0
      : reviewers.reduce((s, r) => s + metric(r), 0);
  const teamResponses =
    view === "all"
      ? data?.team_responses ?? 0
      : reviewers.reduce((s, r) => s + respMetric(r), 0);
  const leader = reviewers[0];
  // Day highlighted in the daily chart: the selected day, else the best day.
  const highlightDay = view === "all" ? data?.best_day ?? -1 : view;

  const switchPeriod = (p: LeaderboardPeriod) => {
    if (p === period) return;
    setPeriod(p);
    setView("all");
  };
  const stepMonth = (delta: number) => {
    setMonth((m) => shiftMonth(m, delta));
    setView("all");
  };

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            href="/"
            className="text-xs font-medium text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark dark:hover:text-storesight-accent-light"
          >
            ← Back home
          </Link>
          <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
            <TrophyIcon className="h-6 w-6 text-storesight-accent dark:text-storesight-accent-light" />
            {isMonth ? `${monthName(month)} leaderboard` : "This week\u2019s leaderboard"}
          </h1>
          <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            {isMonth
              ? "Jobs completed · full calendar month · the weekly board still resets Monday"
              : `Jobs completed · week of ${data?.week_start ?? "—"} · resets Monday`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-storesight-border p-0.5 dark:border-storesight-border-dark" role="tablist">
            {(["week", "month"] as const).map((p) => (
              <button
                key={p}
                type="button"
                role="tab"
                aria-selected={period === p}
                onClick={() => switchPeriod(p)}
                className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                  period === p
                    ? "bg-storesight-primary/10 text-storesight-primary dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
                    : "text-storesight-ink-muted hover:text-storesight-primary dark:text-storesight-ink-muted-dark"
                }`}
              >
                {p === "week" ? "Weekly" : "Monthly"}
              </button>
            ))}
          </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-storesight-border px-3 py-1.5 text-xs font-medium text-storesight-primary-dark transition hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:text-storesight-ink-dark dark:hover:border-storesight-accent-light"
        >
          Refresh
        </button>
        </div>
      </div>

      {isMonth && (
        <div className="mb-4 flex items-center gap-2">
          <button
            type="button"
            onClick={() => stepMonth(-1)}
            aria-label="Previous month"
            className="rounded-lg border border-storesight-border px-2.5 py-1 text-xs font-medium text-storesight-ink-muted transition hover:border-storesight-primary/40 dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark"
          >
            ←
          </button>
          <div className="min-w-32 text-center text-sm font-medium text-storesight-ink dark:text-storesight-ink-dark">
            {monthName(month)}
          </div>
          <button
            type="button"
            onClick={() => stepMonth(1)}
            disabled={isCurrentMonth}
            aria-label="Next month"
            className="rounded-lg border border-storesight-border px-2.5 py-1 text-xs font-medium text-storesight-ink-muted transition hover:border-storesight-primary/40 disabled:cursor-not-allowed disabled:opacity-40 dark:border-storesight-border-dark dark:text-storesight-ink-muted-dark"
          >
            →
          </button>
          {!isCurrentMonth && (
            <button
              type="button"
              onClick={() => { setMonth(currentMonthKey()); setView("all"); }}
              className="text-xs font-medium text-storesight-primary hover:underline dark:text-storesight-accent-light"
            >
              This month
            </button>
          )}
          {loading && (
            <span className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">Loading…</span>
          )}
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {error}
        </div>
      )}

      {allReviewers.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white p-10 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark">
          No reviews logged {periodLabel}. Standings appear as reviewers mark jobs done.
        </div>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-1.5">
            <RangeChip active={view === "all"} onClick={() => setView("all")}>
              {isMonth ? "Month" : "Week"}
            </RangeChip>
            {isMonth ? (
              // 31 chips would crowd the row; pick a day from the chart below.
              view !== "all" ? (
                <RangeChip active onClick={() => setView("all")}>
                  {dayLabel(view)} ×
                </RangeChip>
              ) : (
                <span className="ml-1 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                  Click a day in the chart to see just that day
                </span>
              )
            ) : (
              (data?.day_labels ?? []).map((lbl, i) => (
                <RangeChip key={i} active={view === i} today={i === todayIdx} onClick={() => setView(i)}>
                  {lbl}
                </RangeChip>
              ))
            )}
          </div>

          {reviewers.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-storesight-border bg-white p-8 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark">
              No jobs reviewed on {scopeLabel} yet.
            </div>
          ) : (
          <>
          <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label={`Jobs ${scopeLabel}`} value={teamTotal} />
            <StatCard label={`Responses ${scopeLabel}`} value={teamResponses} />
            <StatCard
              label="Top reviewer"
              value={leader ? metric(leader) : 0}
              sub={leader ? leader.name.split(" ")[0] : undefined}
            />
            <StatCard label="Active reviewers" value={reviewers.length} />
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="rounded-2xl border border-storesight-border bg-white p-5 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
              <div className="mb-4 text-xs font-medium uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                Top of the board
              </div>
              <Podium
                entries={top3.map((r) => ({
                  email: r.email,
                  name: r.name,
                  color: r.color,
                  count: metric(r),
                  responses: respMetric(r),
                }))}
              />
            </div>

            <div className="rounded-2xl border border-storesight-border bg-white p-5 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
              <div className="mb-4 text-xs font-medium uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                Daily team output
              </div>
              <div className={`flex items-end justify-between ${isMonth ? "gap-0.5" : "gap-2"}`} style={{ height: 150 }}>
                {(data?.totals_by_day ?? []).map((v, i) => (
                  <div
                    key={i}
                    className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1.5"
                    title={isMonth ? `${dayLabel(i)}: ${v} jobs` : undefined}
                  >
                    {!isMonth && (
                      <div className="text-[11px] font-medium tabular-nums text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        {v}
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => setView(i)}
                      aria-label={`Show ${dayLabel(i)}`}
                      className={`w-full rounded-t-md transition-[height] ${
                        highlightDay === i
                          ? "bg-storesight-accent dark:bg-storesight-accent-light"
                          : "bg-storesight-accent/35 hover:bg-storesight-accent/55 dark:bg-storesight-accent/40"
                      }`}
                      style={{ height: `${Math.max(4, (v / maxDay) * 110)}px` }}
                    />
                    <div className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {/* Month view: label day 1 and every 5th so they don't collide. */}
                      {isMonth ? (i === 0 || (i + 1) % 5 === 0 ? data?.day_labels[i] : "\u00a0") : data?.day_labels[i]}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-2xl border border-storesight-border bg-white p-5 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
            <div className="mb-3 text-xs font-medium uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              Full standings
            </div>
            <div className="flex flex-col gap-2.5">
              {reviewers.map((r, i) => {
                const c = colorFor(r);
                return (
                  <div key={r.email} className="flex items-center gap-3">
                    <div className="w-5 text-right text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {i + 1}
                    </div>
                    <div
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-medium text-white"
                      style={{ backgroundColor: c }}
                    >
                      {initials(r.name)}
                    </div>
                    <div className="w-32 truncate text-sm text-storesight-ink dark:text-storesight-ink-dark">
                      {r.name}
                    </div>
                    <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-storesight-bg-tint dark:bg-storesight-surface-dark">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${Math.round((metric(r) / max) * 100)}%`, backgroundColor: c }}
                      />
                    </div>
                    <div className="w-24 text-right text-sm tabular-nums text-storesight-ink dark:text-storesight-ink-dark">
                      <span className="font-semibold">{metric(r)}</span>
                      <span className="text-storesight-ink-muted dark:text-storesight-ink-muted-dark"> jobs</span>
                      <span className="ml-1.5 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        {respMetric(r).toLocaleString()} resp
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          </>
          )}
        </>
      )}
    </div>
  );
}

function RangeChip({
  active,
  today,
  onClick,
  children,
}: {
  active: boolean;
  today?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative rounded-lg px-3 py-1.5 text-xs font-medium transition ${
        active
          ? "border border-storesight-primary bg-storesight-primary/10 text-storesight-primary dark:border-storesight-accent-light dark:bg-storesight-accent/20 dark:text-storesight-accent-light"
          : "border border-storesight-border bg-white text-storesight-ink-muted hover:border-storesight-primary/40 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark"
      }`}
    >
      {children}
      {today && (
        <span
          aria-hidden
          className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-storesight-accent dark:bg-storesight-accent-light"
        />
      )}
    </button>
  );
}

function TrophyIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M8 4h8v5a4 4 0 0 1-8 0V4Z M8 5H5v2a3 3 0 0 0 3 3 M16 5h3v2a3 3 0 0 1-3 3 M9 17h6 M10 17v-2.2 M14 17v-2.2 M8 21h8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function StatCard({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="rounded-2xl border border-storesight-border bg-white p-4 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
      <div className="text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">{label}</div>
      <div className="mt-0.5 text-2xl font-semibold text-storesight-ink dark:text-storesight-ink-dark">
        {value.toLocaleString()}
        {sub && <span className="ml-1.5 text-xs font-normal text-storesight-ink-muted dark:text-storesight-ink-muted-dark">{sub}</span>}
      </div>
    </div>
  );
}
