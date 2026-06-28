export type Row = {
  id: string;
  projectId: string;
  projectName: string;
  jobId: string | null;
  groupIds: string[];
  priority: number;
  name: string;
  unreviewedCount: number;
  oldestSubmission: string;
  extras: Record<string, unknown>;
  completedAt?: string | null;
};

export type ProjectSummary = {
  projectId: string;
  projectName: string;
  jidCount: number;
  oldestSubmission: string;
};

export type ShiftSnapshot = {
  id: string;
  published_at: string;
  published_by: string;
  assignments: Record<string, Row[]>;
};

export type MyTasksResponse = {
  snapshot_id: string | null;
  published_at?: string;
  /** Signed-in reviewer's assigned color, if any. */
  color?: string | null;
  rows: Row[];
  /** >0 when this load just cleared the queue and handed out a fresh batch. */
  refilled?: number;
};

export type Reviewer = {
  id: string;
  name: string;
  email: string;
  /** Admin-assigned accent color (hex). When absent, a stable color is derived from the email. */
  color?: string;
};

export type ReviewerSlot = {
  /** Reviewer.id this slot refers to (empty string until selected). */
  reviewerId: string;
  /** Target task count (admin-editable, auto-rebalanced). */
  count: number;
  /** When true, auto-rebalance leaves this slot's count alone. */
  locked: boolean;
};

export type ShiftDraft = {
  /** Up to 6 slots. */
  slots: ReviewerSlot[];
  /** Sum target for the shift. When assignAll, binds to the pool size. */
  totalTarget: number;
  /** When true, totalTarget auto-tracks the available pool. */
  assignAll: boolean;
  /**
   * Projects pinned to specific reviewers for this shift. Keyed by
   * `reviewerId` → list of `projectId`s. Pinned rows are drawn from the
   * pool first before priority top-up.
   */
  projectPins: Record<string, string[]>;
};

export const MAX_SLOTS_PER_SHIFT = 100;

export const SHIFT_ACCENTS = [
  "#4e339c",
  "#7554c2",
  "#9c5cff",
  "#c386ff",
  "#ffa450",
  "#00b8a3",
] as const;

export function accentFor(index: number): string {
  return SHIFT_ACCENTS[index % SHIFT_ACCENTS.length];
}

/**
 * Palette offered when an admin assigns a color to a reviewer. Wider and more
 * distinct than SHIFT_ACCENTS so reviewers are easy to tell apart at a glance.
 */
export const REVIEWER_COLORS = [
  "#7554c2", // purple
  "#9c5cff", // violet
  "#3b82f6", // blue
  "#0ea5e9", // sky
  "#00b8a3", // teal
  "#16a34a", // green
  "#ffa450", // amber
  "#d97706", // orange
  "#e0457b", // pink
  "#db2777", // magenta
  "#ef4444", // red
  "#64748b", // slate
] as const;

/** Stable color derived from an email, used when no explicit color is assigned. */
export function colorForEmail(email: string): string {
  const e = (email || "").trim().toLowerCase();
  let h = 0;
  for (let i = 0; i < e.length; i++) h = (h * 31 + e.charCodeAt(i)) >>> 0;
  return REVIEWER_COLORS[h % REVIEWER_COLORS.length];
}

/** Resolve a reviewer's display color: explicit assignment first, else derived from email. */
export function reviewerColor(
  reviewer?: { color?: string; email?: string } | null,
): string {
  if (reviewer?.color) return reviewer.color;
  if (reviewer?.email) return colorForEmail(reviewer.email);
  return REVIEWER_COLORS[0];
}

export function emptyShiftDraft(): ShiftDraft {
  return {
    slots: [],
    totalTarget: 0,
    assignAll: false,
    projectPins: {},
  };
}
