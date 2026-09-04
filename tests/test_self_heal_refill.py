"""Tests for the GET-time self-heal refill on /api/shifts/my.

The completion POST finish-check only fires on the incomplete→complete edge.
When that edge is missed the reviewer has nothing left to click, so nothing can
re-trigger it. The GET-time self-heal is the re-entry point: a reviewer showing
zero pending work gets a refill attempt on their next page load.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402

REVIEWER = "sam@storesight.com"


def _make_dev_token(email, name="User"):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {"email": email, "name": name, "iat": now,
         "exp": now + datetime.timedelta(hours=8)},
        "irrelevant", algorithm="HS256",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    token_file = token_dir / "dev-token"
    token_file.write_text(_make_dev_token(REVIEWER, "Sam"))
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def _setup(monkeypatch, rows, completed_job_ids, refill_calls, refill_return):
    monkeypatch.setattr(main, "_latest_snapshot", lambda: ("snap1", {}))
    monkeypatch.setattr(
        main, "_rows_for_reviewer", lambda snap, email, force=False: list(rows)
    )
    monkeypatch.setattr(
        main, "_list_completions_for_snapshot",
        lambda snap, reviewer_email=None, force=False: [
            {"job_id": j, "completed_at": "2026-09-04T00:00:00Z"}
            for j in completed_job_ids
        ],
    )
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [
        {"id": "r1", "name": "Sam", "email": REVIEWER},
    ])
    # Live feed: every assigned job still has reviewable work, so nothing is
    # dropped by the auto-rejected filter.
    monkeypatch.setattr(
        main.bloom, "fetch_prioritized_jobs",
        lambda *a, **k: [
            {"jobId": r["jobId"], "unreviewedCount": 5, "extras": {"newCount": 5}}
            for r in rows
        ],
    )

    def fake_refill(snap_id, email, count):
        refill_calls.append((snap_id, email, count))
        return list(refill_return)

    monkeypatch.setattr(main, "_auto_refill_reviewer", fake_refill)


def test_refills_and_returns_new_rows_when_nothing_pending(client, monkeypatch):
    """All assigned jobs done → refill fires and the new rows come back inline."""
    calls = []
    _setup(
        monkeypatch,
        rows=[{"jobId": "A", "name": "Job A"}, {"jobId": "B", "name": "Job B"}],
        completed_job_ids=["A", "B"],
        refill_calls=calls,
        refill_return=[{"jobId": "C", "name": "Job C", "unreviewedCount": 7}],
    )

    resp = client.get("/api/shifts/my")
    assert resp.status_code == 200
    rows = resp.get_json()["data"]["rows"]

    assert len(calls) == 1, "refill should be attempted exactly once"
    assert calls[0][1] == REVIEWER
    # The refilled job is present and pending, alongside the two completed ones.
    by_id = {r["jobId"]: r for r in rows}
    assert set(by_id) == {"A", "B", "C"}
    assert by_id["C"]["completedAt"] is None
    assert by_id["C"]["unreviewedCount"] == 7


def test_no_refill_while_work_is_still_pending(client, monkeypatch):
    """One job still open → this is a normal read, no refill."""
    calls = []
    _setup(
        monkeypatch,
        rows=[{"jobId": "A", "name": "Job A"}, {"jobId": "B", "name": "Job B"}],
        completed_job_ids=["A"],
        refill_calls=calls,
        refill_return=[{"jobId": "C"}],
    )

    resp = client.get("/api/shifts/my")
    assert resp.status_code == 200
    assert calls == [], "must not refill a reviewer who still has work"


def test_no_refill_for_reviewer_with_no_assignments(client, monkeypatch):
    """Someone not in this shift has 0 rows and 0 pending — not a finished queue."""
    calls = []
    _setup(
        monkeypatch, rows=[], completed_job_ids=[],
        refill_calls=calls, refill_return=[{"jobId": "C"}],
    )

    resp = client.get("/api/shifts/my")
    assert resp.status_code == 200
    assert calls == [], "must not refill someone who was never assigned work"


def test_cooldown_prevents_refetching_on_every_page_load(client, monkeypatch):
    """An empty pool must not be re-attempted on each poll of the page."""
    calls = []
    _setup(
        monkeypatch,
        rows=[{"jobId": "A", "name": "Job A"}],
        completed_job_ids=["A"],
        refill_calls=calls,
        refill_return=[],  # pool genuinely exhausted
    )

    for _ in range(3):
        assert client.get("/api/shifts/my").status_code == 200

    assert len(calls) == 1, "cooldown should collapse repeat attempts into one"


def test_read_still_succeeds_when_refill_raises(client, monkeypatch):
    """A refill blowing up must never take down the reviewer's task list."""
    calls = []
    _setup(
        monkeypatch,
        rows=[{"jobId": "A", "name": "Job A"}],
        completed_job_ids=["A"],
        refill_calls=calls,
        refill_return=[],
    )

    def boom(snap_id, email, count):
        calls.append((snap_id, email, count))
        raise RuntimeError("bloom exploded")

    monkeypatch.setattr(main, "_auto_refill_reviewer", boom)

    resp = client.get("/api/shifts/my")
    assert resp.status_code == 200
    assert len(calls) == 1
    assert [r["jobId"] for r in resp.get_json()["data"]["rows"]] == ["A"]


def test_cooldown_helper_is_per_reviewer():
    """One reviewer's attempt must not block another's."""
    assert main._self_heal_cooldown_ok("snap1", "a@storesight.com") is True
    assert main._self_heal_cooldown_ok("snap1", "a@storesight.com") is False
    assert main._self_heal_cooldown_ok("snap1", "b@storesight.com") is True


def test_a_post_refill_suppresses_the_next_self_heal():
    """The finish-check stamps the cooldown, so the page load right after a
    finish doesn't redo the uncached Bloom fetch the POST just did."""
    main._mark_refill_attempted("snap1", REVIEWER)
    assert main._self_heal_cooldown_ok("snap1", REVIEWER) is False
    # A different reviewer is unaffected.
    assert main._self_heal_cooldown_ok("snap1", "other@storesight.com") is True
