"""Work finished earlier today stays with whoever finished it — across shifts —
but only while they're still on the current one.

Production 2026-11-09: the morning shift was cleared and a new one published
at 10:40. Ownership only read the current snapshot, so 15 jobs finished that
morning went to a different reviewer once new responses landed on them.
"""

import datetime
import os

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import internal_api  # noqa: E402

ME = "sam@storesight.com"
SAYLOR = "saylor@storesight.com"
BLAKE = "blake@storesight.com"


def _today():
    return datetime.datetime.now(main._SHIFT_TZ).date().isoformat()


def _checkoff(email, job, project="", day=None):
    return {"id": f"log-{email}-{job}", "data": {
        "kind": main.CHECKOFF_LOG_KIND, "reviewer_email": email, "job_id": job,
        "project_id": project, "local_date": day or _today(),
        "shift_snapshot_id": "morning"}}


def _feed(*jobs):
    return [{"id": j, "jobId": j, "projectId": p, "name": f"Job {j}",
             "unreviewedCount": 2, "extras": {"newCount": 2}}
            for j, p in jobs]


def _refill(monkeypatch, feed, on_shift, log):
    """Top up ME on a fresh snapshot where `on_shift` (plus ME) hold no rows."""
    stored = []
    shift_docs = [{"id": f"rs-{i}", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap2",
        "reviewer_email": rev, "part": 0, "batch_size": 5, "rows": []}}
        for i, rev in enumerate([ME, *on_shift])]
    snap = {"id": "snap2", "data": {"kind": "shift_snapshot", "prioritization_flags": {}}}
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
        "reviewer_shift": shift_docs, "shift_snapshot": [snap],
        main.CHECKOFF_LOG_KIND: log,
    }.get(kind, []))
    monkeypatch.setattr(main, "_list_completions_for_snapshot", lambda *a, **k: [])
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: feed)
    monkeypatch.setattr(main.bloom, "is_excluded_client", lambda c: False)
    monkeypatch.setattr(internal_api, "post",
                        lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})
    monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "cache_upsert_doc", lambda *a, **k: None)
    return sorted(r["jobId"] for r in main._auto_refill_reviewer("snap2", ME, 5))


def test_a_job_finished_on_an_earlier_shift_stays_with_its_finisher(monkeypatch):
    # 1975893: Saylor finished it at 8:55, Laurel was topped up with it at 11:57.
    feed = _feed(("1975893", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(SAYLOR, "1975893", "p1")])
    assert added == ["fresh"]


def test_its_project_stays_with_them_too(monkeypatch):
    feed = _feed(("other_in_p1", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(SAYLOR, "1975893", "p1")])
    assert added == ["fresh"]


def test_it_comes_back_to_the_reviewer_who_finished_it(monkeypatch):
    feed = _feed(("mine", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(ME, "mine", "p1")])
    assert added == ["fresh", "mine"]


def test_someone_who_has_left_releases_their_work(monkeypatch):
    # Blake finished it this morning but isn't on the current shift (closed
    # out, or a morning-only shift), so it's the team's again.
    feed = _feed(("blakes", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(BLAKE, "blakes", "p1")])
    assert added == ["blakes", "fresh"]


def test_yesterdays_checkoffs_dont_count(monkeypatch):
    feed = _feed(("old", "p1"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(SAYLOR, "old", "p1", day="2000-01-01")])
    assert added == ["old"]


def test_auto_publish_skips_a_teammates_finished_work(monkeypatch):
    jobs, projects = {"j1": {SAYLOR}}, {"p9": {SAYLOR}}
    monkeypatch.setattr(main, "_finished_today_by", lambda on_shift: (
        (jobs, projects) if SAYLOR in on_shift else ({}, {})))
    docs = []
    monkeypatch.setattr(internal_api, "post", lambda path, json=None: (
        docs.append(json["data"]) or {"data": {"id": f"d{len(docs)}"}}))
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: [])
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(main, "_get_auto_publish_enabled", lambda *a, **k: True)
    monkeypatch.setattr(main, "_get_todays_scheduled_reviewers", lambda **k: [ME, SAYLOR])
    monkeypatch.setattr(main, "_send_slack_notification", lambda *a, **k: None)
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: _feed(
        ("j1", "p1"), ("j2", "p9"), ("j3", "p3"), ("j4", "p4")))

    main.app.config["TESTING"] = True
    with main.app.test_request_context("/api/shifts/auto-publish", method="POST"):
        main.api_shifts_auto_publish()
    held = {d["reviewer_email"]: sorted(r["jobId"] for r in d["rows"])
            for d in docs if d.get("kind") == "reviewer_shift"}
    # j1 and j2 (Saylor's job and project) only ever land with Saylor.
    assert set(held.get(ME, [])) <= {"j3", "j4"}
    assert held.get(ME), "sanity: Sam still gets the unowned work"
