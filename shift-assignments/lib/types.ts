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
  rows: Row[];
};

export type Reviewer = {
  id: string;
  name: string;
  email: string;
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

export function emptyShiftDraft(): ShiftDraft {
  return {
    slots: [],
    totalTarget: 0,
    assignAll: false,
    projectPins: {},
  };
}
