"use client";

import { useSyncExternalStore } from "react";
import type { Row } from "./types";

/**
 * Which jobs this reviewer has already opened in Collection Review today.
 *
 * Checking a job off takes a moment after Open in Review (Bloom has to catch
 * up), so without a marker it's easy to lose track of what you've already
 * looked at. Kept in this browser only, and reset on a new local day — the
 * same day boundary a shift expires on.
 */
const KEY = "my-tasks:opened-reviews";
const EVENT = "opened-reviews-change";

type Stored = { day: string; ids: string[] };

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

let cache: { raw: string | null; ids: Set<string> } | null = null;

function read(): Set<string> {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    // Storage blocked — the marker just won't show.
  }
  if (cache && cache.raw === raw) return cache.ids;
  let ids = new Set<string>();
  try {
    const parsed = raw ? (JSON.parse(raw) as Stored) : null;
    if (parsed && parsed.day === today() && Array.isArray(parsed.ids)) ids = new Set(parsed.ids);
  } catch {
    // Corrupt value — start fresh.
  }
  cache = { raw, ids };
  return ids;
}

/** The ids a row stands for: every member job of a By-PID card. */
function rowIds(row: Row): string[] {
  if (row.groupIds?.length) return row.groupIds;
  return [row.jobId || row.projectId || row.id];
}

export function markOpened(row: Row): void {
  const ids = new Set(read());
  rowIds(row).forEach((id) => ids.add(id));
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ day: today(), ids: [...ids] } satisfies Stored));
  } catch {
    return;
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

const EMPTY = new Set<string>();

/** True once every job the row stands for has been opened today. */
export function useOpened(row: Row): boolean {
  const ids = useSyncExternalStore(subscribe, read, () => EMPTY);
  return rowIds(row).every((id) => ids.has(id));
}
