"""Finished work is released to the team.

A job someone checked off — this shift or earlier today — goes to whoever runs
out next once new responses land on it. It used to stay with whoever finished
it (from 2026-10-02, and all day from 2026-11-09), which kept handing
reviewers the same jobs over and over; reverted 2026-11-10. What still holds:
a project a teammate has unfinished work in stays with them, and a job
someone is in review on in FieldAgent goes to them (see test_fa_in_review).
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


def _refill(monkeypatch, feed, on_shift, log, held=None, completed=()):
    """Top up ME on a fresh snapshot. `on_shift` (plus ME) are on it, holding
    the (jobId, projectId) rows in `held` ({email: [...]}, default none);
    `completed` is [(email, jobId)] checked off on this shift."""
    held = held or {}
    stored = []
    shift_docs = [{"id": f"rs-{i}", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap2",
        "reviewer_email": rev, "part": 0, "batch_size": 5,
        "rows": [{"id": j, "jobId": j, "projectId": p, "unreviewedCount": 1}
                 for j, p in held.get(rev, [])]}}
        for i, rev in enumerate([ME, *on_shift])]
    snap = {"id": "snap2", "data": {"kind": "shift_snapshot", "prioritization_flags": {}}}
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
        "reviewer_shift": shift_docs, "shift_snapshot": [snap],
        main.CHECKOFF_LOG_KIND: log,
    }.get(kind, []))
    monkeypatch.setattr(main, "_list_completions_for_snapshot", lambda *a, **k: [
        {"id": f"c-{e}-{j}", "reviewer_email": e, "job_id": j, "shift_snapshot_id": "snap2"}
        for e, j in completed])
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: feed)
    monkeypatch.setattr(main.bloom, "is_excluded_client", lambda c: False)
    monkeypatch.setattr(internal_api, "post",
                        lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})
    monkeypatch.setattr(internal_api, "put", lambda path, json=None: {"data": {}})
    monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "cache_upsert_doc", lambda *a, **k: None)
    return sorted(r["jobId"] for r in main._auto_refill_reviewer("snap2", ME, 5))


def test_a_job_a_teammate_finished_earlier_today_is_released(monkeypatch):
    feed = _feed(("1975893", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(SAYLOR, "1975893", "p1")])
    assert added == ["1975893", "fresh"]


def test_its_project_is_released_too(monkeypatch):
    feed = _feed(("other_in_p1", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(SAYLOR, "1975893", "p1")])
    assert added == ["fresh", "other_in_p1"]


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


def test_a_job_a_teammate_holds_unfinished_stays_theirs(monkeypatch):
    LIAM = "liam@storesight.com"
    feed = _feed(("1976141", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[LIAM],
                    log=[_checkoff(ME, "1976141", "p1")],
                    held={LIAM: [("1976141", "p1")]})
    assert added == ["fresh"]


def test_a_job_a_teammate_checked_off_this_shift_is_released(monkeypatch):
    LIAM = "liam@storesight.com"
    feed = _feed(("1976141", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[LIAM], log=[],
                    held={LIAM: [("1976141", "p1")]},
                    completed=[(LIAM, "1976141")])
    assert added == ["1976141", "fresh"]


def test_my_morning_checkoff_doesnt_open_a_project_a_teammate_holds_now(monkeypatch):
    LIAM = "liam@storesight.com"
    feed = _feed(("other_in_p1", "p1"), ("fresh", "p2"))
    added = _refill(monkeypatch, feed, on_shift=[LIAM],
                    log=[_checkoff(ME, "morning_in_p1", "p1")],
                    held={LIAM: [("liams_in_p1", "p1")]})
    assert added == ["fresh"]


def test_two_people_who_both_finished_it_earlier_can_either_get_it(monkeypatch):
    feed = _feed(("shared", "p1"))
    added = _refill(monkeypatch, feed, on_shift=[SAYLOR],
                    log=[_checkoff(ME, "shared", "p1"), _checkoff(SAYLOR, "shared", "p1")])
    assert added == ["shared"]


def test_auto_publish_deals_finished_work_to_anyone(monkeypatch):
    docs = []
    monkeypatch.setattr(internal_api, "post", lambda path, json=None: (
        docs.append(json["data"]) or {"data": {"id": f"d{len(docs)}"}}))
    log = [_checkoff(SAYLOR, "j1", "p1"), _checkoff(SAYLOR, "j2", "p2")]
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: (
        log if kind == main.CHECKOFF_LOG_KIND else []))
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(main, "_get_auto_publish_enabled", lambda *a, **k: True)
    monkeypatch.setattr(main, "_get_todays_scheduled_reviewers", lambda **k: [ME, SAYLOR])
    monkeypatch.setattr(main, "_send_slack_notification", lambda *a, **k: None)
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: _feed(
        ("j1", "p1"), ("j2", "p2"), ("j3", "p3"), ("j4", "p4")))

    monkeypatch.setattr(main.roles, "get_role", lambda email: "admin")
    monkeypatch.setattr(main, "_purge_stale_shift_docs", lambda: None)
    main.app.config["TESTING"] = True
    with main.app.test_request_context("/api/shifts/auto-publish", method="POST"):
        from flask import g
        g.user = {"email": "admin@storesight.com"}
        main.api_shifts_auto_publish()
    held = {d["reviewer_email"]: sorted(r["jobId"] for r in d["rows"])
            for d in docs if d.get("kind") == "reviewer_shift"}
    # Saylor's finished jobs are dealt evenly, not reserved for her.
    assert sorted(held.get(ME, []) + held.get(SAYLOR, [])) == ["j1", "j2", "j3", "j4"]
    assert len(held[ME]) == len(held[SAYLOR]) == 2


def test_deal_gives_owned_work_to_an_owner_in_the_batch():
    owners = ({"x": {ME, SAYLOR}}, {"p": {SAYLOR}})
    rows = _feed(("x", "px"), ("in_p", "p"), ("a", "pa"), ("b", "pb"))
    out = main._distribute_evenly(rows, [ME, SAYLOR, BLAKE], owners=owners)
    ids = {e: [r["jobId"] for r in v] for e, v in out.items()}
    # Two owners: it goes to one of them, never dropped from both.
    assert "x" in ids[ME] + ids[SAYLOR] and "x" not in ids[BLAKE]
    assert "in_p" in ids[SAYLOR]
    # Unowned work still spreads to everyone.
    assert ids[BLAKE]


def test_deal_holds_back_work_whose_owner_isnt_in_the_batch():
    # The owner is on the current shift but not in this publish; their own
    # top-up picks it up, and nobody else is short-changed in its place.
    owners = ({"x": {SAYLOR}}, {})
    rows = _feed(("x", "px"), ("a", "pa"), ("b", "pb"))
    out = main._distribute_evenly(rows, [ME, BLAKE], owners=owners)
    ids = sorted(r["jobId"] for v in out.values() for r in v)
    assert ids == ["a", "b"]
