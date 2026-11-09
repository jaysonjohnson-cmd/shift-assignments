import type { Row } from "./types";

export const COLLECTION_REVIEW_URL =
  "https://prod.fieldagent.net/admin/fieldagent/collection-review/";

/**
 * FieldAgent's own "Close My Assignments" link (on Search Agent Submissions).
 * It closes the signed-in user's open review assignments, so it has to run in
 * the reviewer's browser on their own FieldAgent session — the Internal API
 * has no way to do it. Without it, a reviewer who ends their shift here stays
 * "in review" in FieldAgent and teammates handed their jobs see them there
 * (2026-11-09: Laurel still in review on 1975675 after it went to Liam).
 */
export const CLOSE_MY_ASSIGNMENTS_URL =
  "https://prod.fieldagent.net/admin/fieldagent/assignments/close/";

/** How long the close window stays up — long enough for the request to land. */
export const CLOSE_WINDOW_MS = 3000;

/**
 * Hit Close My Assignments without leaving a FieldAgent tab behind: a small
 * window that closes itself after CLOSE_WINDOW_MS.
 *
 * It can't be invisible. FieldAgent only knows who's asking from its session
 * cookie, and browsers don't send that on a background fetch or iframe from
 * another site — only on a real visit to the page. So it's a visit, kept short.
 * Must be called synchronously in a click (browsers block windows opened after
 * an await). Returns false if the window was blocked, so the caller can fall
 * back to a plain link.
 */
export function closeFieldAgentAssignments(
  open: typeof window.open = (...a) => window.open(...a),
  later: (fn: () => void, ms: number) => unknown = setTimeout,
): boolean {
  const w = open(
    CLOSE_MY_ASSIGNMENTS_URL,
    "fa-close-assignments",
    "popup,width=420,height=240",
  );
  if (!w) return false;
  // Same effect as noopener, but keeps our handle so we can close it.
  try {
    w.opener = null;
  } catch {
    /* ignore */
  }
  later(() => {
    try {
      w.close();
    } catch {
      /* already closed */
    }
  }, CLOSE_WINDOW_MS);
  return true;
}

/**
 * The Collection Review link a reviewer opens for one of their rows.
 *
 * Scoped to the exact job when there is one — passing the project too makes
 * Collection Review open the whole project (every JID) and reviewers overlap.
 * Only a multi-job "By PID" group (no jobId) falls back to the project.
 *
 * On a split job the link carries `work_max`, the reviewer's share, so
 * Collection Review stops them at their share instead of whatever loads they
 * pull. Added by FieldAgent on 2026-10-19 for exactly this; before it, one
 * sharer's export pulled the whole job, teammate's share included.
 */
export function collectionReviewUrl(row: Row): string {
  const params = new URLSearchParams();
  if (row.jobId) {
    params.set("job", row.jobId);
    // Only while a teammate is still on it: once they've all checked off,
    // whatever is left is this reviewer's, and a cap would strand it.
    const share = row.splitShare ?? 0;
    const teammateOnIt = (row.splitPartners ?? []).some((p) => !p.done);
    if (teammateOnIt && share > 0) {
      params.set("work_max", String(share));
    }
  } else {
    const pid = row.projectId || row.id;
    if (pid) params.set("project", pid);
  }
  const qs = params.toString();
  return qs ? `${COLLECTION_REVIEW_URL}?${qs}#/` : COLLECTION_REVIEW_URL;
}
