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
  it("deduplicates by projectId (PID)", () => {
    // Scenario: Same project has 2 jobs (different JIDs)
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1", 1), // PID-1, Job 1
      mockRow("2", "PID-1", "JID-2", 2), // PID-1, Job 2 (duplicate PID)
      mockRow("3", "PID-2", "JID-3", 3), // PID-2, Job 1
      mockRow("4", "PID-3", "JID-4", 4), // PID-3, Job 1
    ];

    const draft = mockShift(["Saylor", "Laurel"]);
    const result = assignShift(pool, draft);

    // Collect all PIDs assigned across reviewers
    const assignedPids = new Set<string>();
    const pidsPerReviewer: Record<string, Set<string>> = {
      Saylor: new Set(),
      Laurel: new Set(),
    };

    for (const [reviewer, rows] of Object.entries(result.assignments)) {
      for (const row of rows) {
        const pid = row.projectId;
        assignedPids.add(pid);
        pidsPerReviewer[reviewer].add(pid);
      }
    }

    // Verify no PID is assigned to both reviewers
    for (const pid of assignedPids) {
      const saylorsCount = pidsPerReviewer["Saylor"].has(pid) ? 1 : 0;
      const laurelCount = pidsPerReviewer["Laurel"].has(pid) ? 1 : 0;
      expect(saylorsCount + laurelCount).toBeLessThanOrEqual(
        1,
        `PID ${pid} assigned to multiple reviewers`
      );
    }

    // Verify PID-1 only appears once (JID-1, not JID-2)
    const allAssigned = Object.values(result.assignments).flat();
    const pid1Jobs = allAssigned.filter((r) => r.projectId === "PID-1");
    expect(pid1Jobs.length).toBe(1);
    expect(pid1Jobs[0].jobId).toBe("JID-1"); // First (highest priority) wins
  });

  it("prioritizes highest-priority jobs per PID", () => {
    // Scenario: PID-1 has 2 jobs with different priorities
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1-low", 10), // Lower priority
      mockRow("2", "PID-1", "JID-1-high", 1), // Higher priority (goes first)
      mockRow("3", "PID-2", "JID-2", 2),
    ];

    const draft = mockShift(["Saylor"]);
    const result = assignShift(pool, draft);

    const pid1Jobs = Object.values(result.assignments)
      .flat()
      .filter((r) => r.projectId === "PID-1");

    expect(pid1Jobs.length).toBe(1);
    expect(pid1Jobs[0].jobId).toBe("JID-1-high");
  });

  it("moves duplicate PIDs to leftover", () => {
    const pool: Row[] = [
      mockRow("1", "PID-1", "JID-1", 1),
      mockRow("2", "PID-1", "JID-2", 2), // Duplicate
      mockRow("3", "PID-2", "JID-3", 3),
    ];

    const draft = mockShift(["Saylor"]);
    const result = assignShift(pool, draft);

    // JID-2 should be in leftover (duplicate PID)
    const leftoverJids = result.leftover.map((r) => r.jobId);
    expect(leftoverJids).toContain("JID-2");
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
