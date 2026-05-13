// Quick sanity check for the assignment engine against hand-picked rows
// matching Gemini's ground-truth "Mass Review Assignee" column from the
// Priority Page Export.
import { assign } from "../lib/assign.ts";
import { DEFAULT_RULES } from "../lib/types.ts";

const base = {
  projectId: "",
  jobId: null,
  groupIds: [],
  unreviewedCount: 0,
  oldestSubmission: "",
  extras: {},
};

const rows = [
  { id: "A", priority: 1, name: "Any project", ...base },
  { id: "B", priority: 50, name: "Boundary 50", ...base },
  { id: "C", priority: 51, name: "Boundary 51", ...base },
  { id: "D", priority: 100, name: "Boundary 100", ...base },
  { id: "E", priority: 101, name: "P&G Essentials Study", ...base },
  { id: "F", priority: 113, name: "Walmart Q2 Reset", ...base },
  { id: "G", priority: 119, name: "Sam's Club Pricing", ...base },
  { id: "H", priority: 121, name: "Costco Seasonal", ...base },
  { id: "I", priority: 102, name: "Retail Pipeline - Grocery", ...base },
  { id: "J", priority: 150, name: "Unmatched Misc", ...base },
];

const expected = {
  A: "m-1",
  B: "m-1",
  C: "m-2",
  D: "m-2",
  E: "aft-1",
  F: "aft-2",
  G: "aft-3",
  H: "aft-3",
  I: "aft-4",
  J: "aft-1", // overflow
};

const out = assign(rows, DEFAULT_RULES);
let pass = true;
for (const [bucketId, bucketRows] of Object.entries(out)) {
  for (const r of bucketRows) {
    const exp = expected[r.id];
    const ok = exp === bucketId;
    if (!ok) pass = false;
    console.log(`${ok ? "✓" : "✗"} ${r.id} (p=${r.priority}) "${r.name}" → ${bucketId}${ok ? "" : ` (expected ${exp})`}`);
  }
}
console.log(pass ? "\nALL PASS" : "\nFAILURES");
process.exit(pass ? 0 : 1);
