import type { Row } from "./types";

export const COLLECTION_REVIEW_URL =
  "https://prod.fieldagent.net/admin/fieldagent/collection-review/";

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
