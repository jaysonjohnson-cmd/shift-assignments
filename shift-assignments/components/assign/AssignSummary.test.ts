import { describe, expect, it } from "vitest";
import { summarizeShift } from "./AssignSummary";
import type { Reviewer, Row, ShiftDraft } from "@/lib/types";

function row(jobId: string): Row {
  return {
    id: jobId,
    projectId: `P-${jobId}`,
    projectName: "",
    jobId,
    groupIds: [],
    priority: 1,
    name: `Job ${jobId}`,
    unreviewedCount: 5,
    oldestSubmission: "",
    extras: {},
  };
}

const reviewers: Reviewer[] = [
  { id: "r1", name: "Ryan", email: "ryan@storesight.com" },
  { id: "r2", name: "Grayson", email: "grayson@storesight.com" },
];

function draft(counts: Record<string, number>): ShiftDraft {
  return {
    slots: Object.entries(counts).map(([reviewerId, count]) => ({
      reviewerId,
      count,
      locked: false,
    })),
    totalTarget: Object.values(counts).reduce((a, b) => a + b, 0),
    assignAll: false,
    projectPins: {},
  };
}

describe("summarizeShift", () => {
  it("records what was asked for so a short batch is visible", () => {
    // The real case: 15 and 20 requested, 9 and 13 delivered, with nothing
    // in the UI saying so.
    const assignments = {
      r1: ["1", "2", "3", "4", "5", "6", "7", "8", "9"].map(row),
      r2: ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m"].map(row),
    };

    const lines = summarizeShift("Shift", assignments, reviewers, draft({ r1: 15, r2: 20 }));

    expect(lines.find((l) => l.reviewerId === "r1")).toMatchObject({ count: 9, requested: 15 });
    expect(lines.find((l) => l.reviewerId === "r2")).toMatchObject({ count: 13, requested: 20 });
  });

  it("keeps a reviewer who got nothing", () => {
    // Zero rows used to drop the reviewer from the summary — the one case
    // most worth seeing.
    const lines = summarizeShift("Shift", { r1: [] }, reviewers, draft({ r1: 15 }));

    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ reviewerName: "Ryan", count: 0, requested: 15 });
  });

  it("leaves requested unset when no draft is passed", () => {
    // Without a draft there is nothing to compare against, so the banner
    // must stay quiet rather than invent a shortfall.
    const lines = summarizeShift("Shift", { r1: [row("1")] }, reviewers);

    expect(lines[0].requested).toBeUndefined();
  });
});

describe("the @/ alias resolves runtime imports", () => {
  it("imports a value, not just a type", async () => {
    // Guards vitest.config.ts: type-only imports would pass without it.
    const { reviewerColor } = await import("@/lib/types");
    expect(typeof reviewerColor).toBe("function");
  });
});
