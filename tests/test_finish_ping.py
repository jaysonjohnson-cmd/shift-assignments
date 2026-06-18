"""Tests for the 'reviewer finished all assignments' Slack ping.

The completion endpoint should ping the admin only when a reviewer's LAST
remaining assignment is marked done, and never when work still remains.
Storage + Slack calls are mocked so nothing hits the live API.
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


def _setup_common(monkeypatch, posted, existing_completions):
    """Wire up a reviewer with two assigned projects (A, B)."""
    monkeypatch.setenv("SLACK_NOTIFY_CHANNEL", "C0TEST")
    monkeypatch.setattr(main, "_latest_snapshot", lambda: ("snap1", {}))
    monkeypatch.setattr(
        main,
        "_rows_for_reviewer",
        lambda snap, email: [{"projectId": "A"}, {"projectId": "B"}],
    )
    monkeypatch.setattr(
        main,
        "_list_completions_for_snapshot",
        lambda snap, reviewer_email=None: existing_completions,
    )

    def fake_post(path, json=None):
        posted.append((path, json))
        if path.endswith("/api/slack/post"):
            return {"data": {"ok": True}}
        return {"data": {"id": "completion-doc-1"}}  # storage create

    monkeypatch.setattr(internal_api, "post", fake_post)


def test_ping_fires_on_last_assignment(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [
        {"id": "r1", "name": "Sam", "email": "sam@storesight.com"},
    ])
    posted = []
    # Project A already done; completing B finishes the queue.
    _setup_common(monkeypatch, posted, [{"project_id": "A"}])

    resp = c.post("/api/shifts/my/complete", json={"project_id": "B"})
    assert resp.status_code == 201, resp.get_json()

    slack_calls = [p for p in posted if p[0].endswith("/api/slack/post")]
    assert len(slack_calls) == 1
    body = slack_calls[0][1]
    assert body["channel"] == "C0TEST"
    assert "Sam" in body["text"]
    assert "2 assignments" in body["text"]


def test_no_ping_when_work_remains(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [])
    posted = []
    # Nothing done yet; completing A still leaves B pending.
    _setup_common(monkeypatch, posted, [])

    resp = c.post("/api/shifts/my/complete", json={"project_id": "A"})
    assert resp.status_code == 201, resp.get_json()

    slack_calls = [p for p in posted if p[0].endswith("/api/slack/post")]
    assert slack_calls == []


def test_no_ping_when_channel_unset(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("sam@storesight.com", "Sam"))
    monkeypatch.setattr(main.roles, "list_reviewers", lambda: [])
    posted = []
    _setup_common(monkeypatch, posted, [{"project_id": "A"}])
    monkeypatch.delenv("SLACK_NOTIFY_CHANNEL", raising=False)  # no channel configured

    resp = c.post("/api/shifts/my/complete", json={"project_id": "B"})
    assert resp.status_code == 201, resp.get_json()

    slack_calls = [p for p in posted if p[0].endswith("/api/slack/post")]
    assert slack_calls == []  # silently skipped, completion still succeeds
