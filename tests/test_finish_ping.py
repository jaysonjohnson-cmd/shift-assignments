"""Tests for auto-refill + Slack ping when a reviewer finishes their queue.

When a reviewer marks their LAST assignment done, the tool should auto-assign
the same number of fresh jobs (skipping anything already assigned to anyone)
and post a Slack ping. Storage, Bloom, and Slack calls are mocked.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import internal_api  # noqa: E402


def _make_dev_token(email, name="User"):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + datetime.timedelta(hours=8),
    }
    return jwt.encode(payload, "irrelevant", algorithm="HS256")


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    token_file = token_dir / "dev-token"
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c, token_file


def _setup_endpoint(monkeypatch, posted, existing_completions, refill_calls, refill_return):
    """Reviewer with two assigned projects (A, B); refill is stubbed."""
    monkeypatch.setenv("SLACK_NOTIFY_CHANNEL", "C0TEST")
    monkeypatch.setattr(main, "_latest_snapshot", lambda: ("snap1", {}))
    monkeypatch.setattr(
        main,
        "_rows_for_reviewer",
        lambda snap, email, force=False: [{"jobId": "A"}, {"jobId": "B"}],
    )
    monkeypatch.setattr(
        main,
        "_list_completions_for_snapshot",
        lambda snap, reviewer_email=None, force=False: existing_completions,
    )
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [
        {"id": "r1", "name": "Sam", "email": "sam@storesight.com"},
    ])

    def fake_refill(snap_id, email, count):
        refill_calls.append((snap_id, email, count))
        return refill_return

    monkeypatch.setattr(main, "_auto_refill_reviewer", fake_refill)

    def fake_post(path, json=None):
        posted.append((path, json))
        if path.endswith("/api/slack/post"):
            return {"data": {"ok": True}}
        return {"data": {"id": "completion-doc-1"}}

    monkeypatch.setattr(internal_api, "post", fake_post)


def test_finish_triggers_refill_and_ping(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    posted, refill_calls = [], []
    # A already done; completing B finishes the queue. Refill returns 2 jobs.
    _setup_endpoint(monkeypatch, posted, [{"job_id": "A"}], refill_calls,
                    refill_return=[{"jobId": "X"}, {"jobId": "Y"}])

    resp = c.post("/api/shifts/my/complete", json={"job_id": "B"})
    assert resp.status_code == 201, resp.get_json()

    # Refill was asked for the same count they started with (2).
    assert refill_calls == [("snap1", "sam@storesight.com", 2)]
    slack = [p for p in posted if p[0].endswith("/api/slack/post")]
    assert len(slack) == 1
    text = slack[0][1]["text"]
    assert "Sam" in text and "2 assignments" in text and "auto-assigned 2" in text


def test_no_refill_or_ping_when_work_remains(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    posted, refill_calls = [], []
    _setup_endpoint(monkeypatch, posted, [], refill_calls, refill_return=[])

    resp = c.post("/api/shifts/my/complete", json={"job_id": "A"})
    assert resp.status_code == 201, resp.get_json()

    assert refill_calls == []  # B still pending → not finished → no refill
    assert [p for p in posted if p[0].endswith("/api/slack/post")] == []


def test_refill_runs_but_no_ping_when_channel_unset(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    posted, refill_calls = [], []
    _setup_endpoint(monkeypatch, posted, [{"job_id": "A"}], refill_calls,
                    refill_return=[{"jobId": "X"}, {"jobId": "Y"}])
    monkeypatch.delenv("SLACK_NOTIFY_CHANNEL", raising=False)

    resp = c.post("/api/shifts/my/complete", json={"job_id": "B"})
    assert resp.status_code == 201, resp.get_json()

    assert refill_calls == [("snap1", "sam@storesight.com", 2)]  # still auto-assigns
    assert [p for p in posted if p[0].endswith("/api/slack/post")] == []  # just no ping


def test_finish_check_reads_completions_authoritatively(client, monkeypatch):
    """Regression: the finish check must read completions with force=True so a
    fast finisher (whose earlier completions haven't landed in the warm cache
    yet) still triggers the ping. Previously it reused a stale cached list and
    silently skipped the Slack ping."""
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    posted, refill_calls = [], []
    _setup_endpoint(monkeypatch, posted, [{"job_id": "A"}], refill_calls,
                    refill_return=[{"jobId": "X"}, {"jobId": "Y"}])

    force_flags = []
    real = main._list_completions_for_snapshot

    def spy(snap, reviewer_email=None, force=False):
        force_flags.append(force)
        return [{"job_id": "A"}]

    monkeypatch.setattr(main, "_list_completions_for_snapshot", spy)

    resp = c.post("/api/shifts/my/complete", json={"job_id": "B"})
    assert resp.status_code == 201, resp.get_json()
    # The finish check must have made at least one authoritative (force=True) read.
    assert True in force_flags


# ---------- the refill logic itself ----------


def test_auto_refill_excludes_already_assigned(monkeypatch):
    """Refill skips jobs already on anyone's queue and stores the next N."""
    shift_docs = [
        {"id": "d1", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap1",
                               "reviewer_email": "sam@storesight.com",
                               "rows": [{"jobId": "J1"}, {"jobId": "J2"}], "part": 0}},
        {"id": "d2", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap1",
                               "reviewer_email": "kim@storesight.com",
                               "rows": [{"jobId": "J3"}], "part": 0}},
    ]
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: shift_docs)
    feed = [
        {"id": j, "jobId": j, "projectId": f"p{j}", "priority": 1, "name": j,
         "unreviewedCount": 3, "oldestSubmission": ""}
        for j in ["J1", "J2", "J3", "J4", "J5", "J6"]
    ]
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda: feed)

    stored = []
    monkeypatch.setattr(internal_api, "post", lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})

    added = main._auto_refill_reviewer("snap1", "sam@storesight.com", 2)

    # J1-J3 are taken; the next two unassigned are J4, J5.
    assert [r["jobId"] for r in added] == ["J4", "J5"]
    # Stored as a new chunk appended after sam's existing part 0.
    assert len(stored) == 1
    doc = stored[0]["data"]
    assert doc["reviewer_email"] == "sam@storesight.com"
    assert doc["part"] == 1
    assert [r["jobId"] for r in doc["rows"]] == ["J4", "J5"]


def test_auto_refill_uses_stored_batch_size_not_grown_queue(monkeypatch):
    """The refill batch is the original allotment (batch_size), not the
    accumulated queue — so finishing never doubles the next refill. Sam started
    with a batch of 2 but has grown to 4 assigned jobs; the refill must add 2."""
    shift_docs = [
        {"id": "d0", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap1",
                              "reviewer_email": "sam@storesight.com",
                              "rows": [{"jobId": "J1"}, {"jobId": "J2"}],
                              "part": 0, "batch_size": 2}},
        {"id": "d1", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap1",
                              "reviewer_email": "sam@storesight.com",
                              "rows": [{"jobId": "J3"}, {"jobId": "J4"}],
                              "part": 1, "batch_size": 2}},
    ]
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: shift_docs)
    feed = [
        {"id": j, "jobId": j, "projectId": f"p{j}", "priority": 1, "name": j,
         "unreviewedCount": 3, "oldestSubmission": ""}
        for j in ["J1", "J2", "J3", "J4", "J5", "J6", "J7"]
    ]
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda: feed)
    stored = []
    monkeypatch.setattr(internal_api, "post", lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})

    # fallback_count is 4 (grown queue) — but stored batch_size=2 must win.
    added = main._auto_refill_reviewer("snap1", "sam@storesight.com", 4)

    assert [r["jobId"] for r in added] == ["J5", "J6"]  # 2 fresh, not 4
    doc = stored[0]["data"]
    assert doc["part"] == 2
    assert doc["batch_size"] == 2  # persisted onto the refill chunk


def test_auto_refill_returns_empty_when_feed_exhausted(monkeypatch):
    shift_docs = [
        {"id": "d1", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap1",
                              "reviewer_email": "sam@storesight.com",
                              "rows": [{"jobId": "J1"}], "part": 0}},
    ]
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: shift_docs)
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs",
                        lambda: [{"id": "J1", "jobId": "J1"}])  # only the taken job
    posted = []
    monkeypatch.setattr(internal_api, "post", lambda path, json=None: posted.append(json) or {"data": {}})

    added = main._auto_refill_reviewer("snap1", "sam@storesight.com", 5)
    assert added == []
    assert posted == []  # nothing stored
