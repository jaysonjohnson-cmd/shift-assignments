"use client";

import { useEffect } from "react";
import { listReviewers } from "./api";
import { useStore } from "./store";

/** Fetch the server-stored reviewers list into the Zustand store on mount. */
export function useReviewerSync() {
  const setReviewers = useStore((s) => s.setReviewers);
  useEffect(() => {
    let cancelled = false;
    listReviewers()
      .then((rs) => {
        if (!cancelled) setReviewers(rs);
      })
      .catch(() => {
        // Silently fall back to empty list — the Settings page is where the
        // user will see the real error.
      });
    return () => {
      cancelled = true;
    };
  }, [setReviewers]);
}
