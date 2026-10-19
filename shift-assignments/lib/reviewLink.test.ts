import { describe, expect, it } from "vitest";
import { collectionReviewUrl } from "./reviewLink";
import type { Row } from "./types";

const row = (over: Partial<Row> = {}): Row => ({
  id: "1", projectId: "P1", projectName: "", jobId: "1974041", groupIds: [],
  priority: 1, name: "Job", unreviewedCount: 150, oldestSubmission: "", extras: {},
  ...over,
});

describe("collectionReviewUrl", () => {
  it("opens the exact job, uncapped, for a normal row", () => {
    expect(collectionReviewUrl(row())).toBe(
      "https://prod.fieldagent.net/admin/fieldagent/collection-review/?job=1974041#/",
    );
  });

  it("caps a split job at the reviewer's share", () => {
    const url = collectionReviewUrl(row({
      splitShare: 75,
      splitPartners: [{ email: "zach@x.com", name: "Zach", done: false }],
    }));
    expect(url).toBe(
      "https://prod.fieldagent.net/admin/fieldagent/collection-review/?job=1974041&work_max=75#/",
    );
  });

  it("lifts the cap once every teammate has checked off", () => {
    const url = collectionReviewUrl(row({
      splitShare: 75,
      splitPartners: [{ email: "zach@x.com", name: "Zach", done: true }],
    }));
    expect(url).not.toContain("work_max");
  });

  it("drops the cap once the split has dissolved", () => {
    expect(collectionReviewUrl(row({ splitShare: 75, splitPartners: [] }))).not.toContain("work_max");
  });

  it("falls back to the project for a By-PID group", () => {
    expect(collectionReviewUrl(row({ jobId: null }))).toBe(
      "https://prod.fieldagent.net/admin/fieldagent/collection-review/?project=P1#/",
    );
  });
});
