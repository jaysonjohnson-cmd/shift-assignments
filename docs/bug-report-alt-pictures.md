# Bug report: responses with "alt"-marked pictures don't appear in review

**Reported by:** QC team (via QC Shift Assignments tool owner, jayson.johnson@storesight.com)
**Date:** 2026-06-25
**Product area:** FieldAgent admin — Collection Review & Response Search (`prod.fieldagent.net/admin/fieldagent/...`)
**Severity:** High — reviewers cannot see/clear all submitted work, so jobs can't be fully reviewed.

## Summary

When a job has pictures that are marked **alt** (alternate), some of that job's responses
do not show up in the review screens. Reviewers need to see **all** responses for a job
regardless of whether any of their pictures are marked alt.

## Where it happens

Missing in **both** review entry points:

- **Collection Review** — `https://prod.fieldagent.net/admin/fieldagent/collection-review/?job=<JOB_ID>&project=<PROJECT_ID>#/`
- **Response Search** — `https://prod.fieldagent.net/admin/fieldagent/responseSearch/?job_id=<JOB_ID>&resp_status=N`

Note: Collection Review is opened with **no status filter** (only `job` + `project`), yet the
alt-affected responses are still hidden — so the filtering is happening inside the review
UI/query, not from any caller-supplied filter.

## Steps to reproduce

1. Open a job that has at least one response containing pictures marked **alt**.
2. Open the job in Collection Review (and/or Response Search).
3. Compare the count/list of responses shown against the job's total submitted responses.

## Expected

All submitted responses for the job appear and are reviewable, including responses that
contain one or more pictures marked alt.

## Actual

Responses associated with alt-marked pictures do not appear in the review list, so they
can't be reviewed or cleared.

## Evidence / investigation

The QC Shift Assignments tool only deep-links into the FieldAgent review pages; it does not
fetch or render responses or pictures. To locate the layer, we inspected the upstream data via
`GET /api/responsegroups`:

- Example job inspected: **job_id 1966336** (project_id 1633085).
- 17 response groups returned. Status breakdown: `N`=5, `A`=5, `EXP`=3, `CNL`=3, `DEL`=1.
- The response-group records expose `status`, `tp_review_status`, agent/location/timestamps,
  etc. — **no field at the response-group level indicates "alt."** "alt" appears to be a
  **picture-level** attribute (on the individual photos/objectives within a response group).

This suggests the review screens are excluding responses based on a **picture-level alt flag**,
rather than on the response group's own status. Because Collection Review is invoked without a
status filter and the responses are still hidden, the exclusion logic lives in the review
query/UI itself.

## Request

Ensure Collection Review and Response Search return/display **all** responses for a job
regardless of whether their pictures are marked alt. If alt is intended to filter the *picture*
view, it should not remove the whole *response* from the reviewer's list.

If there is an existing URL/query parameter to force alt-inclusive results in Collection Review,
please share it — the QC Shift Assignments tool can append it to its deep links as an interim
workaround while the underlying behavior is fixed.

## Contact

jayson.johnson@storesight.com — happy to screen-share a live repro and provide more job IDs.
