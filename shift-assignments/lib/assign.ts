import type { ReviewerSlot, Row, ShiftDraft } from "./types";

export type ShiftResult = {
  /** Rows assigned per slot, keyed by reviewerId. Slots with empty reviewerId are skipped. */
  assignments: Record<string, Row[]>;
  /** Rows that were not placed into any slot (remain available / overflow). */
  leftover: Row[];
};

/** Even split: total=10, n=3 → [4, 3, 3] (front-loaded remainder). */
export function evenSplit(total: number, n: number): number[] {
  if (n <= 0) return [];
  const safeTotal = Math.max(0, Math.floor(total));
  const base = Math.floor(safeTotal / n);
  const rem = safeTotal - base * n;
  return Array.from({ length: n }, (_, i) => base + (i < rem ? 1 : 0));
}

/**
 * Recompute slot counts given that `editedIndex`'s count was just set by the
 * admin. Locked slots and the edited slot keep their counts; unlocked slots
 * (excluding the edited one) split the remainder evenly. Remainder is clamped
 * to >= 0; if negative, other slots are zeroed and the edited slot stays as
 * entered (the UI clamps the total, not individual inputs).
 */
export function rebalance(
  slots: ReviewerSlot[],
  editedIndex: number,
  totalTarget: number,
): ReviewerSlot[] {
  const target = Math.max(0, Math.floor(totalTarget));
  const fixedSum = slots.reduce((acc, s, i) => {
    if (i === editedIndex || s.locked) return acc + Math.max(0, s.count);
    return acc;
  }, 0);
  const remaining = Math.max(0, target - fixedSum);
  const unlockedIdxs = slots
    .map((s, i) => ({ s, i }))
    .filter(({ s, i }) => i !== editedIndex && !s.locked)
    .map(({ i }) => i);

  const shares = evenSplit(remaining, unlockedIdxs.length);
  return slots.map((s, i) => {
    if (i === editedIndex) return { ...s, count: Math.max(0, Math.floor(s.count)) };
    if (s.locked) return s;
    const idx = unlockedIdxs.indexOf(i);
    return { ...s, count: idx >= 0 ? shares[idx] : 0 };
  });
}

/** Distribute even counts across all unlocked slots up to totalTarget. */
export function evenDistribute(
  slots: ReviewerSlot[],
  totalTarget: number,
): ReviewerSlot[] {
  const target = Math.max(0, Math.floor(totalTarget));
  const lockedSum = slots.reduce((a, s) => a + (s.locked ? Math.max(0, s.count) : 0), 0);
  const remaining = Math.max(0, target - lockedSum);
  const unlockedIdxs = slots
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => !s.locked)
    .map(({ i }) => i);
  const shares = evenSplit(remaining, unlockedIdxs.length);
  return slots.map((s, i) => {
    if (s.locked) return s;
    const idx = unlockedIdxs.indexOf(i);
    return { ...s, count: idx >= 0 ? shares[idx] : 0 };
  });
}

/**
 * Walk `pool` using round-robin distribution (pool is assumed to be sorted
 * highest-priority first). Pinned projects are honored first: any row whose
 * `projectId` is pinned to a reviewer goes to that reviewer, and the slot's
 * count auto-bumps to at least the pinned-row count. Remaining slot capacity
 * is filled by cycling through reviewers, spreading tasks evenly rather than
 * frontloading early reviewers. Slots with empty reviewerId are dropped
 * (their would-be rows fall into leftover).
 */
export function assignShift(pool: Row[], draft: ShiftDraft, prioritizeNew = false, balanceByResponses = false): ShiftResult {
  const pins = draft.projectPins ?? {};

  // Guarantee each job is handed to at most one reviewer. The upstream feed
  // can return more than one record for the same job (e.g. one per group),
  // which would otherwise let the same jobId land on multiple reviewers and
  // overlap the team. Collapse duplicates by jobId here. The pool is assumed
  // pre-sorted highest-priority first, so the first occurrence we keep is the
  // most urgent one.
  const seenJobKeys = new Set<string>();
  const dedupedPool: Row[] = [];
  for (const row of pool) {
    const key = String(row.jobId || row.id || "");
    if (key) {
      if (seenJobKeys.has(key)) continue;
      seenJobKeys.add(key);
    }
    dedupedPool.push(row);
  }

  // reviewerId for each pinned projectId (first slot wins if somehow dup'd).
  const pinOwner = new Map<string, string>();
  for (const slot of draft.slots) {
    if (!slot.reviewerId) continue;
    for (const pid of pins[slot.reviewerId] ?? []) {
      if (!pinOwner.has(pid)) pinOwner.set(pid, slot.reviewerId);
    }
  }

  const pinnedByReviewer: Record<string, Row[]> = {};
  const unpinned: Row[] = [];
  for (const row of dedupedPool) {
    const owner = pinOwner.get(row.projectId);
    if (owner) {
      (pinnedByReviewer[owner] ??= []).push(row);
    } else {
      unpinned.push(row);
    }
  }

  // Calculate how many unpinned rows each reviewer needs.
  const assignments: Record<string, Row[]> = {};
  const unpinnedNeeded: Record<string, number> = {};
  for (const slot of draft.slots) {
    if (!slot.reviewerId) continue;
    const pinned = pinnedByReviewer[slot.reviewerId] ?? [];
    const wanted = Math.max(Math.floor(slot.count), pinned.length);
    const topUp = Math.max(0, wanted - pinned.length);
    unpinnedNeeded[slot.reviewerId] = topUp;
    assignments[slot.reviewerId] = [...pinned];
  }

  // Weighted Round-Robin with optional two-tier distribution:
  // Tier 1 (optional): "New" responses distributed first (if prioritizeNew=true)
  // Tier 2: Higher-priority jobs spread evenly while respecting each reviewer's capacity.
  const activeSlots = draft.slots.filter((s) => s.reviewerId);
  if (activeSlots.length > 0) {
    // Calculate capacity weights for each reviewer (higher capacity = higher weight)
    const capacityWeights = new Map<string, number>();
    for (const slot of activeSlots) {
      const capacity = Math.max(Math.floor(slot.count), (pinnedByReviewer[slot.reviewerId] ?? []).length);
      capacityWeights.set(slot.reviewerId, capacity);
    }

    // Separate new responses from regular jobs if two-tier distribution is enabled
    let newResponses: Row[] = [];
    let regularJobs = unpinned;

    if (prioritizeNew) {
      // Identify "new" responses by checking if oldestSubmission is recent (within last hour)
      // or by checking extras.isNew flag if available
      const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
      newResponses = unpinned.filter(
        (row) =>
          (row.extras?.isNew === true) ||
          (row.oldestSubmission && row.oldestSubmission > oneHourAgo)
      );
      regularJobs = unpinned.filter((row) => !newResponses.includes(row));
    }

    // Helper function to distribute jobs using weighted round-robin
    const distributeJobs = (jobs: Row[], tier: "new" | "priority") => {
      if (jobs.length === 0) return;

      // If balanceByResponses is enabled, sort jobs by unreviewedCount (descending)
      // to handle high-volume jobs first and distribute them more evenly
      let sortedJobs = [...jobs];
      if (balanceByResponses) {
        sortedJobs.sort((a, b) => (b.unreviewedCount ?? 0) - (a.unreviewedCount ?? 0));
      }

      // Group jobs by priority (higher number = higher priority, but we sort ascending)
      const jobsByPriority: Record<number, Row[]> = {};
      for (const row of sortedJobs) {
        const priority = row.priority ?? 999;
        (jobsByPriority[priority] ??= []).push(row);
      }

      // Process each priority level, distributing with weighted balancing
      const priorityLevels = Object.keys(jobsByPriority)
        .map(Number)
        .sort((a, b) => a - b);

      // Track total response count assigned to each reviewer (for response-aware distribution)
      const responseCountByReviewer = new Map<string, number>();
      for (const slot of activeSlots) {
        const pinnedCount = (pinnedByReviewer[slot.reviewerId] ?? []).reduce(
          (sum, row) => sum + (row.unreviewedCount ?? 0),
          0
        );
        responseCountByReviewer.set(slot.reviewerId, pinnedCount);
      }

      for (const priority of priorityLevels) {
        const jobsAtPriority = jobsByPriority[priority];

        for (const row of jobsAtPriority) {
          let bestReviewer: string | null = null;
          // balanceByResponses picks the lowest metric (start high); the
          // default path picks the highest remaining/capacity ratio (start
          // low). A single Infinity init left the default path dead — no
          // ratio is > Infinity — so it never assigned unpinned jobs.
          let bestMetric = balanceByResponses ? Infinity : -Infinity;

          for (const slot of activeSlots) {
            const remaining = unpinnedNeeded[slot.reviewerId] ?? 0;
            if (remaining <= 0) continue;

            const capacity = capacityWeights.get(slot.reviewerId) ?? 1;

            if (balanceByResponses) {
              // Response-aware: balance by total unreviewedCount, not just job count
              // Assign to reviewer with lowest current response load relative to capacity
              const currentResponseCount = responseCountByReviewer.get(slot.reviewerId) ?? 0;
              const avgResponsesPerJob = currentResponseCount / Math.max(1, (assignments[slot.reviewerId]?.length ?? 0));
              const metric = currentResponseCount / capacity;

              if (metric < bestMetric) {
                bestMetric = metric;
                bestReviewer = slot.reviewerId;
              }
            } else {
              // Original: balance by job count and capacity ratio
              const capacityRatio = remaining / capacity;
              if (capacityRatio > bestMetric) {
                bestMetric = capacityRatio;
                bestReviewer = slot.reviewerId;
              }
            }
          }

          if (bestReviewer) {
            assignments[bestReviewer].push(row);
            unpinnedNeeded[bestReviewer]--;

            // Update response count tracker if balancing by responses
            if (balanceByResponses) {
              const currentCount = responseCountByReviewer.get(bestReviewer) ?? 0;
              responseCountByReviewer.set(bestReviewer, currentCount + (row.unreviewedCount ?? 0));
            }
          }
        }
      }
    };

    // Distribute new responses first (Tier 1), then regular jobs (Tier 2)
    if (prioritizeNew) {
      distributeJobs(newResponses, "new");
    }
    distributeJobs(regularJobs, "priority");
  }

  const leftover = unpinned.slice(
    unpinned.length - Object.values(unpinnedNeeded).reduce((a, b) => a + b, 0)
  );
  return { assignments, leftover };
}

/**
 * Return the count a slot should display given its draft count and current
 * pinned-row total — used by the UI to show the auto-bumped value and a
 * "bumped" chip when `pinnedJidCount > draft.count`.
 */
export function effectiveSlotCount(
  slot: ReviewerSlot,
  pinnedJidCount: number,
): number {
  return Math.max(Math.floor(slot.count), pinnedJidCount);
}

/** Count of pinned JIDs available in `pool` for the given project ids. */
export function countPinnedJids(
  pool: Row[],
  projectIds: readonly string[],
): number {
  if (projectIds.length === 0) return 0;
  const set = new Set(projectIds);
  let n = 0;
  for (const row of pool) {
    if (set.has(row.projectId)) n += 1;
  }
  return n;
}

/** Sum of slot counts (the "planned" total). */
export function plannedTotal(draft: ShiftDraft): number {
  return draft.slots.reduce((a, s) => a + Math.max(0, Math.floor(s.count)), 0);
}
