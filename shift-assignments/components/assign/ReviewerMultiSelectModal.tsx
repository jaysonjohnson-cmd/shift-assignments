"use client";

import { useState, useMemo } from "react";
import type { Reviewer } from "@/lib/types";

export function ReviewerMultiSelectModal({
  reviewers,
  exclude = [],
  onConfirm,
  onCancel,
  maxSelectable,
}: {
  reviewers: Reviewer[];
  exclude?: string[];
  onConfirm: (reviewerIds: string[]) => void;
  onCancel: () => void;
  maxSelectable: number;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const excluded = useMemo(() => new Set(exclude), [exclude]);
  const available = useMemo(
    () => reviewers.filter((r) => !excluded.has(r.id)),
    [reviewers, excluded],
  );

  const canSelectMore = selected.size < maxSelectable;

  const toggleReviewer = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (canSelectMore) {
        next.add(id);
      }
      return next;
    });
  };

  const handleConfirm = () => {
    onConfirm(Array.from(selected));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="mx-4 max-w-md w-full rounded-2xl border border-storesight-border bg-white p-6 shadow-2xl dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <h2 className="text-lg font-semibold text-storesight-ink dark:text-storesight-ink-dark">
          Add reviewers
        </h2>
        <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Select up to {maxSelectable} reviewer{maxSelectable === 1 ? "" : "s"}
        </p>

        <div className="mt-4 max-h-64 space-y-2 overflow-y-auto">
          {available.length === 0 ? (
            <p className="py-4 text-center text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              No reviewers available
            </p>
          ) : (
            available.map((reviewer) => {
              const isSelected = selected.has(reviewer.id);
              return (
                <label
                  key={reviewer.id}
                  className="flex items-center gap-3 rounded-lg border border-storesight-border bg-white/60 px-3 py-2 transition hover:border-storesight-accent dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark/60"
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleReviewer(reviewer.id)}
                    disabled={!isSelected && !canSelectMore}
                    className="h-4 w-4 rounded border-storesight-border accent-storesight-accent disabled:opacity-40 shrink-0"
                  />
                  <span className="flex-1 text-sm font-medium text-storesight-primary-dark dark:text-storesight-ink-dark">
                    {reviewer.name}
                  </span>
                </label>
              );
            })
          )}
        </div>

        <div className="mt-6 flex gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="flex-1 rounded-lg border border-storesight-border bg-white px-4 py-2 text-sm font-medium text-storesight-ink hover:border-storesight-accent hover:text-storesight-primary transition dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={selected.size === 0}
            className="flex-1 rounded-lg border border-transparent bg-storesight-primary px-4 py-2 text-sm font-medium text-white shadow-sm shadow-storesight-primary/30 transition hover:bg-storesight-primary-dark disabled:opacity-50 dark:bg-storesight-accent dark:hover:bg-storesight-accent-light"
          >
            Add {selected.size > 0 && `(${selected.size})`}
          </button>
        </div>
      </div>
    </div>
  );
}
