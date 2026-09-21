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
 * Calculate days until a job's deadline. null when no/invalid endDate; negative = overdue.
 */
function daysUntilDeadline(row: Row): number | null {
  const raw = String(row.extras?.endDate ?? "");
  if (!raw) return null;
  const d = new Date(raw);
  if (isNaN(d.getTime())) return null;
  return Math.ceil((d.getTime() - Date.now()) / 86_400_000);
}

/**
 * Calculate days since oldest unreviewed response. null when no/invalid oldestSubmission.
 */
function daysWaiting(row: Row): number | null {
  const raw = String(row.oldestSubmission ?? "");
  if (!raw) return null;
  const d = new Date(raw);
  if (isNaN(d.getTime())) return null;
  return Math.floor((Date.now() - d.getTime()) / 86_400_000);
}

/**
 * Combined urgency score (0-100) based on deadline pressure and response age.
 * Weights: deadline 60%, wait time 40%.
 * Higher score = more urgent. Threshold for critical: 55+
 */
function urgencyScore(row: Row): number {
  const daysLeft = daysUntilDeadline(row);
  const daysOld = daysWaiting(row);

  // Deadline urgency: 0-100 (overdue=100, >30 days=0)
  let closeScore = 15; // unknown deadline — low baseline
  if (daysLeft !== null) {
    if (daysLeft < 0) closeScore = 100; // overdue
    else if (daysLeft <= 1.5) closeScore = 100; // ≤36 hours
    else if (daysLeft <= 3) closeScore = 90;
    else if (daysLeft <= 7) closeScore = 70;
    else if (daysLeft <= 14) closeScore = 50;
    else if (daysLeft <= 30) closeScore = 30;
    else closeScore = 10;
  }

  // Response age urgency: 0-100 (30+ days=100, 0 days=0)
  let waitScore = 0; // unknown age — not urgent
  if (daysOld !== null) {
    if (daysOld >= 30) waitScore = 100;
    else if (daysOld >= 14) waitScore = 80;
    else if (daysOld >= 7) waitScore = 60;
    else if (daysOld >= 3) waitScore = 40;
    else if (daysOld >= 1) waitScore = 20;
    else waitScore = 0;
  }

  // Blended score: deadline 60%, wait time 40%
  return Math.round(0.6 * closeScore + 0.4 * waitScore);
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
export function assignShift(pool: Row[], draft: ShiftDraft, prioritizeNew = false, balanceByResponses = false, prioritizeUrgency = false, prioritizeAged = false): ShiftResult {
  const pins = draft.projectPins ?? {};

  // Guarantee each job is handed to at most one reviewer. The upstream feed can
  // return more than one record for the same job, which would otherwise let the
  // same jobId land on two reviewers and duplicate the work. The pool is assumed
  // pre-sorted highest-priority first, so the first occurrence kept is the most
  // urgent one.
  //
  // Projects are no longer deduped away. They used to be, to stop two reviewers
  // landing in the same project in Collection Review — but the feed averages ~3
  // jobs per project, so it discarded roughly two thirds of the available work
  // (367 jobs collapsed to 101) and reviewers' counts silently came up short:
  // 15 requested, 9 delivered.
  //
  // A project still only ever goes to ONE reviewer; every job in it now goes
  // there too, rather than all but the first being thrown away. See
  // projectOwner below. That keeps Collection Review safe either way: a
  // My Tasks "By PID" card opens the whole project, so a project split across
  // two reviewers would show each of them the other's responses.
  const seenJobKeys = new Set<string>();
  const dedupedPool: Row[] = [];
  for (const row of pool) {
    const jobKey = String(row.jobId || row.id || "");
    if (jobKey && seenJobKeys.has(jobKey)) continue;
    if (jobKey) seenJobKeys.add(jobKey);
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

    // Separate high-urgency jobs (deadline + response age combined)
    let urgentJobs: Row[] = [];
    if (prioritizeUrgency) {
      // Score threshold: 55+ is high urgency (approaching deadline OR aged responses)
      urgentJobs = regularJobs.filter((row) => urgencyScore(row) >= 55);
      regularJobs = regularJobs.filter((row) => urgencyScore(row) < 55);
    }

    // Separate aged submissions (old_sub flag from Bloom) from fresh jobs
    let agedJobs: Row[] = [];
    if (prioritizeAged) {
      agedJobs = regularJobs.filter((row) => Number(row.extras?.old_sub ?? 0) > 0);
      regularJobs = regularJobs.filter((row) => Number(row.extras?.old_sub ?? 0) === 0);
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

      // Track total response count assigned to each reviewer (for response-aware distribution)
      const responseCountByReviewer = new Map<string, number>();
      for (const slot of activeSlots) {
        const pinnedCount = (pinnedByReviewer[slot.reviewerId] ?? []).reduce(
          (sum, row) => sum + (row.unreviewedCount ?? 0),
          0
        );
        responseCountByReviewer.set(slot.reviewerId, pinnedCount);
      }

      // A project belongs to whoever gets its first job; the rest follow, so a
      // project is never split across reviewers.
      //
      // The claim is what bounds the count. A project's jobs are scattered
      // through the pool by priority rather than sitting together, so an
      // unbounded claim keeps pulling jobs onto a reviewer long after they are
      // full — measured at 177 jobs handed out against 95 requested, one
      // reviewer taking 49 for a 20 request. So a reviewer only claims a
      // project they have room for; the whole project then costs them what
      // they had budgeted.
      const projectOwner = new Map<string, string>();
      // Refilled per pass from the jobs that pass is actually placing. Sizing
      // it from every unpinned job over-charged: a 16-job project with 2 jobs
      // in the pass still reserved 16, so capacity was spent on jobs that were
      // never handed out — 59 of 100 requested, with 222 left in overflow.
      const projectSizes = new Map<string, number>();
      const sizeProjects = (rows: Row[]) => {
        projectSizes.clear();
        for (const row of rows) {
          const key = String(row.projectId || "");
          if (key) projectSizes.set(key, (projectSizes.get(key) ?? 0) + 1);
        }
      };

      const placeRow = (row: Row) => {
        const projectKey = String(row.projectId || "");
        const owner = projectKey ? projectOwner.get(projectKey) : undefined;
        if (owner) {
          // Capacity for the whole project was reserved when it was claimed,
          // so the remaining jobs are already paid for.
          assignments[owner].push(row);
          if (balanceByResponses) {
            const current = responseCountByReviewer.get(owner) ?? 0;
            responseCountByReviewer.set(owner, current + (row.unreviewedCount ?? 0));
          }
          return;
        }

        const projectSize = projectKey ? (projectSizes.get(projectKey) ?? 1) : 1;
        let bestReviewer: string | null = null;
        // balanceByResponses picks the lowest metric (start high); the
        // default path picks the highest remaining/capacity ratio (start
        // low). A single Infinity init left the default path dead — no
        // ratio is > Infinity — so it never assigned unpinned jobs.
        let bestMetric = balanceByResponses ? Infinity : -Infinity;
        // Room for the entire project beats a better balance score. Without
        // this the project still lands somewhere, just on someone who then
        // overshoots by the rest of it.
        let bestFits = false;

        for (const slot of activeSlots) {
          const remaining = unpinnedNeeded[slot.reviewerId] ?? 0;
          if (remaining <= 0) continue;

          const capacity = capacityWeights.get(slot.reviewerId) ?? 1;
          const fits = remaining >= projectSize;

          const metric = balanceByResponses
            // Response-aware: balance by total unreviewedCount, not just job
            // count — lowest current load relative to capacity wins.
            ? (responseCountByReviewer.get(slot.reviewerId) ?? 0) / capacity
            // Original: balance by job count and capacity ratio.
            : remaining / capacity;
          const better = balanceByResponses ? metric < bestMetric : metric > bestMetric;

          if (bestReviewer === null || (fits && !bestFits) || (fits === bestFits && better)) {
            bestMetric = metric;
            bestReviewer = slot.reviewerId;
            bestFits = fits;
          }
        }

        if (bestReviewer) {
          assignments[bestReviewer].push(row);
          // Reserve the entire project now, not one job at a time. Its jobs are
          // scattered through the pool by priority, so charging per job leaves
          // the reviewer looking free and they claim several big projects
          // before any of them is paid for.
          unpinnedNeeded[bestReviewer] -= projectSize;
          if (projectKey) projectOwner.set(projectKey, bestReviewer);

          // Update response count tracker if balancing by responses
          if (balanceByResponses) {
            const currentCount = responseCountByReviewer.get(bestReviewer) ?? 0;
            responseCountByReviewer.set(bestReviewer, currentCount + (row.unreviewedCount ?? 0));
          }
        }
      };

      if (balanceByResponses) {
        // Priority still decides which jobs make the cut when this tier has
        // more jobs than remaining capacity — the top-priority ones by
        // priority order are the ones guaranteed a slot, same as the
        // job-count path below. But WHO gets each of those jobs is decided in
        // one flat pass over the whole tier, heaviest job first. Segmenting
        // that decision by priority (as the job-count path does) let a
        // reviewer's quota fill up entirely during an early, often
        // low-response priority group — locking them out before the
        // high-response jobs in a later group were even considered, so every
        // one of those dumped onto whoever still had open slots.
        const totalCapacity = Object.values(unpinnedNeeded).reduce((a, b) => a + b, 0);
        // Select whole projects, best priority first, until capacity is used.
        // Slicing loose jobs took part of a project while the claim reserved
        // all of it, so the rest of that project was paid for and never placed.
        const byProject = new Map<string, Row[]>();
        for (const row of sortedJobs) {
          const key = String(row.projectId || row.id || "");
          const list = byProject.get(key);
          if (list) list.push(row);
          else byProject.set(key, [row]);
        }
        const bestPriority = (rows: Row[]) =>
          rows.reduce((m, r) => Math.min(m, r.priority ?? 999), 999);
        const projectsByPriority = [...byProject.values()].sort(
          (a, b) => bestPriority(a) - bestPriority(b),
        );
        const toPlace = new Set<Row>();
        let budget = totalCapacity;
        for (const rows of projectsByPriority) {
          if (budget <= 0) break;
          for (const r of rows) toPlace.add(r);
          budget -= rows.length;
        }
        sizeProjects([...toPlace]);
        // Place the biggest projects first. Projects are indivisible, so a late
        // 9-job project landing on whoever has 5 slots left swings their count;
        // packing large-to-small lets the small ones fill the gaps. Priority
        // already decided *which* projects are in `toPlace` — this only orders
        // how they're handed out. Measured on the live feed: every reviewer
        // lands exactly on their number, against a spread of 10 in priority
        // order.
        const selected = projectsByPriority
          .filter((rows) => rows.some((r) => toPlace.has(r)))
          .sort((a, b) => b.length - a.length);
        for (const rows of selected) {
          for (const row of rows) placeRow(row);
        }
      } else {
        // Group jobs by priority (lower number = more urgent) and process
        // each level in order, so higher-priority jobs are the last to be
        // left over when this tier doesn't fully fit.
        sizeProjects(sortedJobs);
        const jobsByPriority: Record<number, Row[]> = {};
        for (const row of sortedJobs) {
          const priority = row.priority ?? 999;
          (jobsByPriority[priority] ??= []).push(row);
        }
        const priorityLevels = Object.keys(jobsByPriority)
          .map(Number)
          .sort((a, b) => a - b);
        for (const priority of priorityLevels) {
          // Same packing within a level: priority still decides which jobs get
          // a slot when capacity is short, size only orders them inside it.
          const levelProjects = new Map<string, Row[]>();
          for (const row of jobsByPriority[priority]) {
            const key = String(row.projectId || row.id || "");
            const list = levelProjects.get(key);
            if (list) list.push(row);
            else levelProjects.set(key, [row]);
          }
          const biggestFirst = [...levelProjects.values()].sort((a, b) => b.length - a.length);
          for (const rows of biggestFirst) {
            for (const row of rows) placeRow(row);
          }
        }
      }
    };

    // Distribute in priority order:
    // Tier 1: High-urgency jobs (deadline + response age combined)
    // Tier 2: New responses (last hour)
    // Tier 3: Aged submissions (with true submission dates if loading)
    // Tier 4: Regular jobs
    if (prioritizeUrgency) {
      distributeJobs(urgentJobs, "new");
    }
    if (prioritizeNew) {
      distributeJobs(newResponses, "new");
    }
    if (prioritizeAged) {
      distributeJobs(agedJobs, "new");
    }
    distributeJobs(regularJobs, "priority");
  }

  // Calculate leftover by tracking which unpinned jobs were actually placed.
  // Can't use slice() because distributed jobs may be scattered throughout the
  // unpinned array when priority reordering happens.
  const placedKeys = new Set<string>();
  for (const rows of Object.values(assignments)) {
    for (const row of rows) {
      const key = String(row.jobId || row.id || "");
      if (key) placedKeys.add(key);
    }
  }
  const leftover = unpinned.filter((r) => {
    const key = String(r.jobId || r.id || "");
    return !placedKeys.has(key);
  });
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
