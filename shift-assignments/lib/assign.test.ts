import { describe, expect, it } from "vitest";
import { assignShift, evenSplit, evenDistribute } from "./assign";
import type { Row, ShiftDraft } from "./types";

// Helper to create a mock Row
function mockRow(id: string, projectId: string, jobId: string, priority = 999): Row {
  return {
    id,
    jobId,
    projectId,
    projectName: `Project ${projectId}`,
    groupIds: [],
    priority,
    name: `Job ${jobId}`,
    unreviewedCount: 5,
    oldestSubmission: "",
    extras: {},
  };
}

// Helper to create a shift draft with reviewers
function mockShift(reviewers: string[]): ShiftDraft {
  return {
    slots: reviewers.map((r) => ({
      reviewerId: r,
      count: 2,
      locked: false,
    })),
    totalTarget: reviewers.length * 2,
    assignAll: false,
    projectPins: {},
  };
}

describe("assignShift", () => {
  it("never splits a project across two reviewers", () => {
    // My Tasks' "By PID" card opens the whole project in Collection Review, so
    // a split project would show each reviewer the other's responses.
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1", 1),
      mockRow("2", "PID-1", "JID-2", 2),
      mockRow("3", "PID-1", "JID-3", 3),
      mockRow("4", "PID-2", "JID-4", 4),
      mockRow("5", "PID-2", "JID-5", 5),
      mockRow("6", "PID-3", "JID-6", 6),
    ];

    const result = assignShift(pool, mockShift(["Saylor", "Laurel"]));

    const ownerOf = new Map<string, string>();
    for (const [reviewer, rows] of Object.entries(result.assignments)) {
      for (const row of rows) {
        const prev = ownerOf.get(row.projectId);
        expect(prev ?? reviewer).toBe(reviewer);
        ownerOf.set(row.projectId, reviewer);
      }
    }

    // ...and the jobs aren't discarded to achieve that.
    const assignedJids = Object.values(result.assignments).flat().map((r) => r.jobId);
    expect(assignedJids.length).toBeGreaterThan(2);
  });

  it("reserves a whole project's capacity when it is claimed", () => {
    // A project's jobs are scattered through the pool by priority, not grouped.
    // Charging capacity per job leaves a reviewer looking free long enough to
    // claim several big projects, and the counts blow out — 177 handed out
    // against 95 requested when this was measured against the live feed.
    const pool: Row[] = [
      // three 4-job projects, interleaved the way priority ordering leaves them
      mockRow("1", "PID-A", "JID-A1", 1),
      mockRow("2", "PID-B", "JID-B1", 2),
      mockRow("3", "PID-C", "JID-C1", 3),
      mockRow("4", "PID-A", "JID-A2", 4),
      mockRow("5", "PID-B", "JID-B2", 5),
      mockRow("6", "PID-C", "JID-C2", 6),
      mockRow("7", "PID-A", "JID-A3", 7),
      mockRow("8", "PID-B", "JID-B3", 8),
      mockRow("9", "PID-C", "JID-C3", 9),
      mockRow("10", "PID-A", "JID-A4", 10),
      mockRow("11", "PID-B", "JID-B4", 11),
      mockRow("12", "PID-C", "JID-C4", 12),
    ];

    // Two reviewers wanting 4 each: room for two of the three projects.
    const draft = mockShift(["Saylor", "Laurel"]);
    draft.slots.forEach((slot) => (slot.count = 4));
    const result = assignShift(pool, draft);

    const total = Object.values(result.assignments).flat().length;
    // 8 requested. Without reservation both reviewers claim every project and
    // all 12 land.
    expect(total).toBeLessThanOrEqual(8);
    expect(result.leftover.length).toBeGreaterThan(0);

    // Whichever projects went out went out whole.
    for (const rows of Object.values(result.assignments)) {
      const pids = new Set(rows.map((r) => r.projectId));
      for (const pid of pids) {
        expect(rows.filter((r) => r.projectId === pid).length).toBe(4);
      }
    }
  });

  it("keeps every job in a project, deduping only by jobId", () => {
    // Projects used to be deduped too, so only one job per PID was assignable.
    // The feed averages ~3 jobs per project, so that discarded most of the
    // work and reviewers' counts came up short.
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1", 1),
      mockRow("2", "PID-1", "JID-2", 2), // same project, different job
      mockRow("3", "PID-2", "JID-3", 3),
      mockRow("4", "PID-3", "JID-4", 4),
    ];

    const draft = mockShift(["Saylor", "Laurel"]);
    const result = assignShift(pool, draft);
    const assigned = Object.values(result.assignments).flat();

    // Both jobs of PID-1 are assignable.
    const jids = assigned.map((r) => r.jobId);
    expect(jids).toContain("JID-1");
    expect(jids).toContain("JID-2");

    // ...but no single job is ever handed to two reviewers.
    expect(new Set(jids).size).toBe(jids.length);
  });

  it("drops a repeated jobId from the pool", () => {
    // The upstream feed can return the same job twice; only the first (most
    // urgent) copy survives.
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1", 1),
      mockRow("2", "PID-1", "JID-1", 9), // same jobId again
      mockRow("3", "PID-2", "JID-2", 3),
    ];

    const draft = mockShift(["Saylor"]);
    const result = assignShift(pool, draft);

    const jids = Object.values(result.assignments).flat().map((r) => r.jobId);
    expect(jids.filter((j) => j === "JID-1").length).toBe(1);
  });

  it("puts jobs that exceed capacity in leftover", () => {
    // leftover is what didn't fit the reviewers' counts — not duplicates,
    // which are removed from the pool before distribution.
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1", 1),
      mockRow("2", "PID-2", "JID-2", 2),
      mockRow("3", "PID-3", "JID-3", 3),
    ];

    const draft = mockShift(["Saylor"]);
    draft.slots[0].count = 1; // room for one job only
    const result = assignShift(pool, draft);

    expect(Object.values(result.assignments).flat().length).toBe(1);
    expect(result.leftover.length).toBe(2);
  });

  it("does not break pinned projects", () => {
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1", 1),
      mockRow("2", "PID-1", "JID-2", 2),
    ];

    const draft: ShiftDraft = {
      slots: [
        { reviewerId: "Saylor", count: 2, locked: false },
        { reviewerId: "Laurel", count: 2, locked: false },
      ],
      totalTarget: 4,
      assignAll: false,
      projectPins: {
        Saylor: ["PID-1"], // Pin PID-1 to Saylor
      },
    };

    const result = assignShift(pool, draft);

    // Both jobs for PID-1 go to Saylor, which is what pinning a project means.
    // The project dedup used to drop JID-2 before this ran, so a pin only ever
    // delivered one job however many the project held.
    const saylorsJobs = result.assignments["Saylor"] ?? [];
    const pid1JobsForSaylor = saylorsJobs.filter((r) => r.projectId === "PID-1");

    expect(pid1JobsForSaylor.length).toBe(2);
    expect(pid1JobsForSaylor.map((r) => r.jobId).sort()).toEqual(["JID-1", "JID-2"]);
    // ...and none of it leaks to the other reviewer.
    expect((result.assignments["Laurel"] ?? []).length).toBe(0);
  });
});

describe("evenSplit", () => {
  it("distributes evenly with front-loaded remainder", () => {
    expect(evenSplit(10, 3)).toEqual([4, 3, 3]);
    expect(evenSplit(11, 3)).toEqual([4, 4, 3]);
    expect(evenSplit(5, 2)).toEqual([3, 2]);
  });

  it("handles edge cases", () => {
    expect(evenSplit(0, 3)).toEqual([0, 0, 0]);
    expect(evenSplit(3, 0)).toEqual([]);
    expect(evenSplit(-5, 3)).toEqual([0, 0, 0]);
  });
});

describe("evenDistribute", () => {
  it("preserves locked slot counts", () => {
    const slots = [
      { reviewerId: "A", count: 2, locked: true },
      { reviewerId: "B", count: 0, locked: false },
      { reviewerId: "C", count: 0, locked: false },
    ];

    const result = evenDistribute(slots, 10);

    expect(result[0].count).toBe(2); // Locked, unchanged
    expect(result[1].count + result[2].count).toBe(8); // Split remainder
  });
});
