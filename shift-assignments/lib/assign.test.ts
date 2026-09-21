import { assignShift, evenSplit, evenDistribute } from "./assign";
import type { Row, ShiftDraft } from "./types";

// Helper to create a mock Row
function mockRow(id: string, projectId: string, jobId: string, priority = 999): Row {
  return {
    id,
    jobId,
    projectId,
    priority,
    title: `Job ${jobId}`,
    unreviewedCount: 5,
    extras: {},
  };
}

// Helper to create a shift draft with reviewers
function mockShift(reviewers: string[]): ShiftDraft {
  return {
    shiftTime: "9:00 AM - 12:00 PM",
    slots: reviewers.map((r) => ({
      reviewerId: r,
      count: 2,
      locked: false,
    })),
    projectPins: {},
  };
}

describe("assignShift", () => {
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
      shiftTime: "9:00 AM - 12:00 PM",
      slots: [
        { reviewerId: "Saylor", count: 2, locked: false },
        { reviewerId: "Laurel", count: 2, locked: false },
      ],
      projectPins: {
        Saylor: ["PID-1"], // Pin PID-1 to Saylor
      },
    };

    const result = assignShift(pool, draft);

    // Both jobs for PID-1 should go to Saylor (pinned)
    const saylorsJobs = result.assignments["Saylor"] ?? [];
    const pid1JobsForSaylor = saylorsJobs.filter((r) => r.projectId === "PID-1");

    // With the new dedup, we should get only the first one
    expect(pid1JobsForSaylor.length).toBe(1);
    expect(pid1JobsForSaylor[0].jobId).toBe("JID-1");
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
