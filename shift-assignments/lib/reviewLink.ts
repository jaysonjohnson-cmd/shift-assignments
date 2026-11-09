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


/**
 * Projects a By-PID card must not group: any project holding a split job the
 * reviewer still shares. The split job keeps its own card (its link carries
 * work_max and goes through the split-open wait), but grouping the project's
 * other jobs made a card whose link opens the whole project, split job
 * included, with neither protection.
 */
export function projectsWithLiveSplit(rows: Row[]): Set<string> {
  const out = new Set<string>();
  for (const r of rows) {
    if (r.splitPartners?.length && r.projectId) out.add(r.projectId);
  }
  return out;
}
