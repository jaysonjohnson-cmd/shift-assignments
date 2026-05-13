"use client";

import type { Reviewer } from "@/lib/types";

export function ReviewerPicker({
  value,
  onChange,
  reviewers,
  exclude,
}: {
  value: string;
  onChange: (reviewerId: string) => void;
  reviewers: Reviewer[];
  /** Reviewer ids already picked in the same shift — hidden from options. */
  exclude?: string[];
}) {
  const blocked = new Set(exclude ?? []);
  const available = reviewers.filter((r) => !blocked.has(r.id) || r.id === value);
  const noReviewers = reviewers.length === 0;
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="min-w-0 flex-1 rounded border border-storesight-border bg-white px-1.5 py-1 text-sm outline-none transition focus:border-storesight-accent dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
    >
      <option value="">
        {noReviewers ? "Add reviewers first…" : "Select reviewer…"}
      </option>
      {available.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name}
        </option>
      ))}
    </select>
  );
}
