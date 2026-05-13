"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { listAllCompletions, resetAllCompletions } from "./api";
import type { Reviewer, Row } from "./types";

type State = {
  rows: Row[];
  sourceLabel: string | null;
  fetchedAt: number | null;
  lastPublishedAt: string | null;
  /** Reviewers come from the server — never persisted. */
  reviewers: Reviewer[];
  completionsByEmail: Record<string, string[]>;
  completionsSnapshotId: string | null;
  completionsLoading: boolean;
  setRows: (rows: Row[], sourceLabel: string) => void;
  clearRows: () => void;
  setLastPublishedAt: (iso: string | null) => void;
  setReviewers: (reviewers: Reviewer[]) => void;
  fetchCompletions: () => Promise<void>;
  resetCompletions: () => Promise<number>;
};

export const useStore = create<State>()(
  persist(
    (set) => ({
      rows: [],
      sourceLabel: null,
      fetchedAt: null,
      lastPublishedAt: null,
      reviewers: [],
      completionsByEmail: {},
      completionsSnapshotId: null,
      completionsLoading: false,
      setRows: (rows, sourceLabel) =>
        set(() => ({ rows, sourceLabel, fetchedAt: Date.now() })),
      clearRows: () =>
        set({ rows: [], sourceLabel: null, fetchedAt: null }),
      setLastPublishedAt: (iso) => set({ lastPublishedAt: iso }),
      setReviewers: (reviewers) => set({ reviewers }),
      fetchCompletions: async () => {
        set({ completionsLoading: true });
        try {
          const { snapshot_id, completions } = await listAllCompletions();
          const grouped: Record<string, string[]> = {};
          for (const c of completions) {
            const key = (c.reviewer_email || "").toLowerCase();
            if (!key) continue;
            (grouped[key] ??= []).push(String(c.project_id));
          }
          set({
            completionsByEmail: grouped,
            completionsSnapshotId: snapshot_id,
            completionsLoading: false,
          });
        } catch {
          set({ completionsLoading: false });
        }
      },
      resetCompletions: async () => {
        const { deleted } = await resetAllCompletions();
        set({ completionsByEmail: {} });
        return deleted;
      },
    }),
    {
      name: "storesight-shift-assignments-v1",
      version: 9,
      storage: createJSONStorage(() => localStorage),
      // reviewers are NOT persisted (server-sourced).
      partialize: (s) => ({
        rows: s.rows,
        sourceLabel: s.sourceLabel,
        fetchedAt: s.fetchedAt,
        lastPublishedAt: s.lastPublishedAt,
      }),
      migrate: (persisted, version) => {
        const p = (persisted ?? {}) as Record<string, unknown>;
        // v9 drops `rules`, `search`, `fileName`, `uploadedAt`. Keep only the
        // fields that map forward.
        const rows = version < 7 ? [] : (p.rows as Row[] | undefined) ?? [];
        const sourceLabel =
          (p.sourceLabel as string | null | undefined) ?? null;
        const fetchedAt =
          (p.fetchedAt as number | null | undefined) ?? null;
        const lastPublishedAt =
          (p.lastPublishedAt as string | null | undefined) ?? null;
        return {
          rows,
          sourceLabel,
          fetchedAt,
          lastPublishedAt,
        } as Partial<State>;
      },
    },
  ),
);
