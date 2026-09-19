"use client";

import { reviewerColor } from "@/lib/types";

export type PodiumEntry = {
  email: string;
  name: string;
  color?: string | null;
  /** Jobs completed in the period being shown. */
  count: number;
  /** Responses cleared in the same period. */
  responses: number;
};

export function initials(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0])
    .filter(Boolean)
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function podiumColor(r: { email: string; color?: string | null }): string {
  return reviewerColor({ color: r.color ?? undefined, email: r.email });
}

export function MedalIcon({
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

/** Top-three podium. Shared by the Leaderboard page and the home tile. */
export function Podium({
  entries,
  compact = false,
}: {
  /** Ranked best-first; extras past three are ignored. */
  entries: PodiumEntry[];
  /** Shorter bars for the home tile, where vertical space is tight. */
  compact?: boolean;
}) {
  const top3 = entries.slice(0, 3);
  // Display order is 2nd, 1st, 3rd, but barHeights is indexed by RANK
  // (0 = winner → tallest), not by where it sits on screen.
  const order = [1, 0, 2].filter((i) => top3[i]);
  const barHeights = compact ? [78, 53, 40] : [130, 88, 66];
  const frameHeight = compact ? 140 : 190;

  return (
    <div className="flex items-end justify-center gap-3" style={{ height: frameHeight }}>
      {order.map((idx) => {
        const r = top3[idx];
        const c = podiumColor(r);
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
              {r.count}
            </div>
            <div className="text-[11px] leading-tight text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              {r.responses.toLocaleString()} resp
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
  );
}
