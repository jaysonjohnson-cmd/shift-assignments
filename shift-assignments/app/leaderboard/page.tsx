"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getLeaderboard, type Leaderboard, type LeaderboardReviewer } from "@/lib/api";
import { useUser } from "@/lib/useUser";
import { reviewerColor } from "@/lib/types";

function initials(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0])
    .filter(Boolean)
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function colorFor(r: LeaderboardReviewer): string {
  return reviewerColor({ color: r.color ?? undefined, email: r.email });
}

export default function LeaderboardPage() {
  const { role, loading: userLoading } = useUser();
  const [data, setData] = useState<Leaderboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // "week" = whole week; a number 0–6 = a single weekday (Mon–Sun).
  const [view, setView] = useState<"week" | number>("week");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getLeaderboard());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load leaderboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!userLoading) load();
  }, [userLoading, load]);

  const canView = role === "admin" || role === "lead";

  if (userLoading || loading) {
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
  // Day index of "today" within the shown week (0=Mon), or -1 if outside it.
  const todayIdx = (() => {
    if (!data?.week_start) return -1;
    const start = new Date(data.week_start + "T00:00:00").getTime();
    const diff = Math.floor((Date.now() - start) / 86_400_000);
    return diff >= 0 && diff <= 6 ? diff : -1;
  })();
  // Metric for the active view: whole-week total, or a single day's count.
  const metric = (r: LeaderboardReviewer) =>
    view === "week" ? r.total : r.days[view] ?? 0;
  // Responses (volume) for the active view, shown alongside the job count.
  const respMetric = (r: LeaderboardReviewer) =>
    view === "week" ? r.responses : r.resp_days?.[view] ?? 0;
  const scopeLabel = view === "week" ? "this week" : (data?.day_labels[view] ?? "");
  // Re-rank by the active metric. In day view, hide reviewers with nothing that day.
  const reviewers = [...allReviewers]
    .filter((r) => (view === "week" ? true : metric(r) > 0))
    .sort((a, b) => metric(b) - metric(a) || a.name.localeCompare(b.name));
  const top3 = reviewers.slice(0, 3);
  const max = (reviewers[0] ? metric(reviewers[0]) : 0) || 1;
  const maxDay = Math.max(1, ...(data?.totals_by_day ?? [0]));
  const teamTotal =
    view === "week"
      ? data?.team_total ?? 0
      : reviewers.reduce((s, r) => s + metric(r), 0);
  const teamResponses =
    view === "week"
      ? data?.team_responses ?? 0
      : reviewers.reduce((s, r) => s + respMetric(r), 0);
  const leader = reviewers[0];
  // Day highlighted in the daily chart: the selected day, else the best day.
  const highlightDay = view === "week" ? data?.best_day ?? -1 : view;
  // Podium display order: 2nd, 1st, 3rd. barHeights is indexed by RANK
  // (0 = winner → tallest), not by display position.
  const podiumOrder = [1, 0, 2].filter((i) => top3[i]);
  const barHeights = [130, 88, 66];

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
            This week&apos;s leaderboard
          </h1>
          <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            Jobs completed · week of {data?.week_start ?? "—"} · resets Monday
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-storesight-border px-3 py-1.5 text-xs font-medium text-storesight-primary-dark transition hover:border-storesight-accent hover:text-storesight-primary dark:border-storesight-border-dark dark:text-storesight-ink-dark dark:hover:border-storesight-accent-light"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-4 py-2 text-sm text-storesight-hot-pink">
          {error}
        </div>
      )}

      {allReviewers.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-storesight-border bg-white p-10 text-center text-sm text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark">
          No reviews logged yet this week. Standings appear as reviewers mark jobs done.
        </div>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-1.5">
            <RangeChip active={view === "week"} onClick={() => setView("week")}>
              Week
            </RangeChip>
            {(data?.day_labels ?? []).map((lbl, i) => (
              <RangeChip key={i} active={view === i} today={i === todayIdx} onClick={() => setView(i)}>
                {lbl}
              </RangeChip>
            ))}
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
              <div className="flex items-end justify-center gap-3" style={{ height: 190 }}>
                {podiumOrder.map((idx) => {
                  const r = top3[idx];
                  const c = colorFor(r);
                  return (
                    <div key={r.email} className="flex w-20 flex-col items-center gap-1.5">
                      <MedalIcon className="h-5 w-5" style={{ color: c }} rank={idx + 1} />
                      <div
                        className="flex h-10 w-10 items-center justify-center rounded-full text-sm font-medium text-white"
                        style={{ backgroundColor: c }}
                      >
                        {initials(r.name)}
                      </div>
                      <div className="text-center text-xs leading-tight text-storesight-ink dark:text-storesight-ink-dark">
                        {r.name.split(" ")[0]}
                      </div>
                      <div className="text-lg font-semibold leading-tight text-storesight-ink dark:text-storesight-ink-dark">
                        {metric(r)}
                      </div>
                      <div className="text-[11px] leading-tight text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                        {respMetric(r).toLocaleString()} resp
                      </div>
                      <div
                        className="w-full rounded-t-lg"
                        style={{
                          height: barHeights[idx],
                          backgroundColor: c,
                          opacity: 0.18,
                          borderBottom: `3px solid ${c}`,
                        }}
                      />
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="rounded-2xl border border-storesight-border bg-white p-5 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark">
              <div className="mb-4 text-xs font-medium uppercase tracking-wide text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                Daily team output
              </div>
              <div className="flex items-end justify-between gap-2" style={{ height: 150 }}>
                {(data?.totals_by_day ?? []).map((v, i) => (
                  <div key={i} className="flex flex-1 flex-col items-center justify-end gap-1.5">
                    <div className="text-[11px] font-medium tabular-nums text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {v}
                    </div>
                    <button
                      type="button"
                      onClick={() => setView(i)}
                      aria-label={`Show ${data?.day_labels[i]}`}
                      className={`w-full rounded-t-md transition-[height] ${
                        highlightDay === i
                          ? "bg-storesight-accent dark:bg-storesight-accent-light"
                          : "bg-storesight-accent/35 hover:bg-storesight-accent/55 dark:bg-storesight-accent/40"
                      }`}
                      style={{ height: `${Math.max(4, (v / maxDay) * 110)}px` }}
                    />
                    <div className="text-[11px] text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {data?.day_labels[i]}
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

function MedalIcon({
  className,
  style,
  rank,
}: {
  className?: string;
  style?: React.CSSProperties;
  rank: number;
}) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} style={style} aria-label={`rank ${rank}`} role="img">
      <path d="M8 3 5 9 M16 3l3 6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="15" r="6" stroke="currentColor" strokeWidth="1.6" />
      <text
        x="12"
        y="15"
        textAnchor="middle"
        dominantBaseline="central"
        fontSize="7"
        fontWeight="600"
        fill="currentColor"
      >
        {rank}
      </text>
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
