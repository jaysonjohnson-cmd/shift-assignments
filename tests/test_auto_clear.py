"""The scheduled end-of-day clear.

Exists as its own endpoint rather than pointing Cloud Scheduler at
/api/shifts/clear, which takes a `mode` — a scheduled caller must not be able to
reach mode="reset" (deletes every shift across all time) or scope a clear to a
single reviewer.
"""

import datetime
import os
from zoneinfo import ZoneInfo

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Signed in as the root admin via the LOCAL_DEV dev-token path."""
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    token_file = token_dir / "dev-token"
    now = datetime.datetime.now(datetime.timezone.utc)
    token_file.write_text(jwt.encode(
        {"email": roles.ROOT_ADMIN_EMAIL, "name": "Admin",
         "iat": now, "exp": now + datetime.timedelta(hours=8)},
        "irrelevant", algorithm="HS256"))
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _today_iso():
    now = datetime.datetime.now(ZoneInfo("America/Chicago")).replace(hour=12, minute=0)
    return now.astimezone(datetime.timezone.utc).isoformat()


@pytest.fixture
def shift(monkeypatch):
    """A live shift: 2 reviewers, 3 rows total, 1 completion."""
    deleted = []
    snap = {"id": "snap-1", "data": {"kind": "shift_snapshot",
                                     "published_at": _today_iso()}}
    shifts = [
        {"id": "rs-sam", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap-1",
                                  "reviewer_email": "sam@x.com",
                                  "rows": [{"jobId": "a"}, {"jobId": "b"}]}},
        {"id": "rs-alex", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap-1",
                                   "reviewer_email": "alex@x.com",
                                   "rows": [{"jobId": "c"}]}},
        {"id": "rs-other", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap-OTHER",
                                    "reviewer_email": "zoe@x.com",
                                    "rows": [{"jobId": "z"}]}},
    ]
    comps = [
        {"id": "c-1", "data": {"kind": "completion", "shift_snapshot_id": "snap-1"}},
        {"id": "c-other", "data": {"kind": "completion", "shift_snapshot_id": "snap-OTHER"}},
    ]
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: {
        "shift_snapshot": [snap], "reviewer_shift": shifts, "completion": comps,
    }.get(kind, []))
    monkeypatch.setattr(main, "_try_delete", lambda did: deleted.append(did))
    monkeypatch.setattr(roles, "invalidate_doc_cache", lambda *a, **k: None)
    return deleted


def test_does_nothing_while_the_switch_is_off(client, monkeypatch, shift):
    """The scheduler job can exist before anyone turns it on."""
    monkeypatch.setattr(main, "_get_auto_clear_enabled", lambda force=False: False)
    resp = client.post("/api/shifts/auto-clear")
    assert resp.status_code == 200
    assert resp.get_json()["skipped"] is True
    assert shift == [], "nothing may be deleted while disabled"


def test_clears_the_current_shift_when_enabled(client, monkeypatch, shift):
    monkeypatch.setattr(main, "_get_auto_clear_enabled", lambda force=False: True)
    resp = client.post("/api/shifts/auto-clear")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["cleared_rows"] == 3
    assert body["cleared_completions"] == 1
    assert set(shift) == {"rs-sam", "rs-alex", "c-1", "snap-1"}


def test_leaves_other_snapshots_alone(client, monkeypatch, shift):
    """Only the current snapshot's docs go — not every shift ever."""
    monkeypatch.setattr(main, "_get_auto_clear_enabled", lambda force=False: True)
    client.post("/api/shifts/auto-clear")
    assert "rs-other" not in shift
    assert "c-other" not in shift


def test_no_shift_is_not_an_error(client, monkeypatch):
    """A run on a day nobody published should be a quiet no-op."""
    monkeypatch.setattr(main, "_get_auto_clear_enabled", lambda force=False: True)
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [])
    resp = client.post("/api/shifts/auto-clear")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["cleared_rows"] == 0


def test_takes_no_parameters(client, monkeypatch, shift):
    """A mode in the body must be ignored, not honoured."""
    monkeypatch.setattr(main, "_get_auto_clear_enabled", lambda force=False: True)
    resp = client.post("/api/shifts/auto-clear", json={"mode": "reset"})
    assert resp.status_code == 200
    # "reset" would have deleted snap-OTHER's docs too; it must not.
    assert "rs-other" not in shift


def test_settings_roundtrip_defaults_off(client, monkeypatch):
    monkeypatch.setattr(main, "_get_auto_clear_enabled", lambda force=False: False)
    resp = client.get("/api/shifts/auto-clear/settings")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["enabled"] is False


def test_settings_write_requires_a_boolean(client, monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda: None)
    resp = client.post("/api/shifts/auto-clear/settings", json={"enabled": "yes"})
    assert resp.status_code == 400
