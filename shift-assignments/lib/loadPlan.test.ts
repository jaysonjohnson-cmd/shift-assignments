import { describe, expect, it } from "vitest";
import { describeLoads, planLoads } from "./loadPlan";

describe("planLoads", () => {
  it("builds a share from Collection Review's load sizes", () => {
    expect(planLoads(75)).toEqual([50, 25]);
    expect(planLoads(150)).toEqual([100, 50]);
    expect(planLoads(100)).toEqual([100]);
    expect(planLoads(400)).toEqual([200, 200]);
  });

  it("rounds to the nearest 25, never below one load", () => {
    expect(planLoads(63)).toEqual([50, 25]);
    expect(planLoads(60)).toEqual([50]);
    expect(planLoads(8)).toEqual([25]);
    expect(planLoads(0)).toEqual([]);
  });
});

describe("describeLoads", () => {
  it("says Search once for a single load", () => {
    expect(describeLoads([100])).toBe("Load size 100, Search once — don't Load more");
  });

  it("changes size only when the next load differs", () => {
    expect(describeLoads([50, 25])).toBe("Load size 50, Search · then 25, Load more");
    expect(describeLoads([200, 200])).toBe("Load size 200, Search · then Load more");
  });
});
