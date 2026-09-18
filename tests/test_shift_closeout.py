"""Tests for a reviewer closing out their shift.

Closing out deletes the reviewer's own reviewer_shift docs — that deletion is
what returns their unfinished jobs to the assignable pool, since
_auto_refill_reviewer builds its exclusion set from the rows held in every
reviewer_shift doc. Completion docs are deliberately kept so the Progress
Tracker and the weekly leaderboard don't lose credit for finished work.

Storage and Slack calls are mocked.
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


def _setup(monkeypatch, deleted, posted, closeouts=None):
    """Sam has jobs A (done) and B (unfinished); Alex has C and must be untouched."""
    monkeypatch.setenv("SLACK_ADMIN_USER_ID", "U0JAYSON")
    monkeypatch.setattr(main, "_latest_snapshot", lambda: ("snap1", {}))
    monkeypatch.setattr(
        main,
        "_rows_for_reviewer",
        lambda snap, email, force=False: [{"jobId": "A"}, {"jobId": "B"}],
    )
    monkeypatch.setattr(
        main,
        "_list_completions_for_snapshot",
        lambda snap, reviewer_email=None, force=False: [
            {"id": "comp-A", "job_id": "A"}
        ],
    )
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [
        {"id": "r1", "name": "Sam", "email": "sam@storesight.com"},
    ])

    docs = {
        "reviewer_shift": [
            {"id": "rs-sam", "data": {"kind": "reviewer_shift",
                                      "shift_snapshot_id": "snap1",
                                      "reviewer_email": "sam@storesight.com",
                                      "rows": [{"jobId": "A"}, {"jobId": "B"}]}},
            {"id": "rs-alex", "data": {"kind": "reviewer_shift",
                                       "shift_snapshot_id": "snap1",
                                       "reviewer_email": "alex@storesight.com",
                                       "rows": [{"jobId": "C"}]}},
        ],
        "shift_closeout": list(closeouts or []),
    }
    monkeypatch.setattr(
        main.roles,
        "list_docs_by_kind",
        lambda kind, force=False: docs.get(kind, []),
    )
    monkeypatch.setattr(main, "_try_delete", lambda doc_id: deleted.append(doc_id))
    monkeypatch.setattr(main.roles, "cache_remove_doc", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "cache_upsert_doc", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "invalidate_doc_cache", lambda *a, **k: None)

    def fake_post(path, json=None):
        posted.append((path, json))
        if path.endswith("/api/slack/post"):
            return {"data": {"ok": True}}
        return {"data": {"id": "closeout-doc-1"}}

    monkeypatch.setattr(internal_api, "post", fake_post)


def test_closeout_releases_unfinished_rows_and_keeps_completions(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    deleted, posted = [], []
    _setup(monkeypatch, deleted, posted)

    resp = c.post("/api/shifts/my/close")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["completed"] == 1
    assert data["released"] == 1  # B was never checked off
    assert data["already_closed"] is False

    # The reviewer's own rows doc is gone — that is what frees job B for the
    # rest of the team. Another reviewer's doc is untouched.
    assert "rs-sam" in deleted
    assert "rs-alex" not in deleted
    # Completion credit survives.
    assert "comp-A" not in deleted


def test_closeout_dms_the_admin(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    deleted, posted = [], []
    _setup(monkeypatch, deleted, posted)

    resp = c.post("/api/shifts/my/close")
    assert resp.status_code == 200, resp.get_json()

    slack = [p for p in posted if p[0].endswith("/api/slack/post")]
    assert len(slack) == 1
    assert slack[0][1]["channel"] == "U0JAYSON"
    text = slack[0][1]["text"]
    assert "Sam" in text
    assert "completed 1 job" in text
    assert "released 1 unfinished job" in text


def test_closeout_succeeds_without_dm_target(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    deleted, posted = [], []
    _setup(monkeypatch, deleted, posted)
    monkeypatch.delenv("SLACK_ADMIN_USER_ID", raising=False)

    resp = c.post("/api/shifts/my/close")
    assert resp.status_code == 200, resp.get_json()
    assert "rs-sam" in deleted  # rows still released
    assert [p for p in posted if p[0].endswith("/api/slack/post")] == []


def test_closeout_is_idempotent(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    deleted, posted = [], []
    _setup(monkeypatch, deleted, posted, closeouts=[
        {"id": "co-1", "data": {"kind": "shift_closeout",
                                "shift_snapshot_id": "snap1",
                                "reviewer_email": "sam@storesight.com"}},
    ])

    resp = c.post("/api/shifts/my/close")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["already_closed"] is True
    # Nothing deleted and no second DM on a repeat call.
    assert deleted == []
    assert [p for p in posted if p[0].endswith("/api/slack/post")] == []


def test_closeout_requires_a_published_shift(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    deleted, posted = [], []
    _setup(monkeypatch, deleted, posted)
    monkeypatch.setattr(main, "_latest_snapshot", lambda: (None, None))

    resp = c.post("/api/shifts/my/close")
    assert resp.status_code == 409
    assert deleted == []
