"""Ending the whole shift is all or nothing, reads Storage directly, and
tells the admin who did it.

On 2026-11-19 two End shift clears deleted the snapshot while leaving the
newest assignments behind (the warm cache didn't have them, or their deletes
failed quietly). The next publish saw no shift running and started a new
one, so the reviewers it left out vanished, and nothing recorded who had
cleared.
"""

import datetime
import os
from zoneinfo import ZoneInfo

import jwt
import pytest
import requests

os.environ["LOCAL_DEV"] = "1"

import internal_api  # noqa: E402
import main  # noqa: E402
import roles  # noqa: E402
from main import app  # noqa: E402

PATH = main._STORAGE_PATH


def _today_iso():
    now = datetime.datetime.now(ZoneInfo("America/Chicago")).replace(hour=9, minute=0)
    return now.astimezone(datetime.timezone.utc).isoformat()


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_file = tmp_path / "dev-token"
    now = datetime.datetime.now(datetime.timezone.utc)
    token_file.write_text(jwt.encode(
        {"email": roles.ROOT_ADMIN_EMAIL, "name": "Admin",
         "iat": now, "exp": now + datetime.timedelta(hours=8)},
        "irrelevant", algorithm="HS256"))
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def storage(monkeypatch):
    """A live shift where the warm cache is missing the newest assignment
    (rs-new), which only a forced read returns."""
    snap = {"id": "snap-1", "data": {"kind": "shift_snapshot", "published_at": _today_iso()}}
    old = {"id": "rs-old", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap-1",
                                    "reviewer_email": "sam@x.com", "rows": [{"jobId": "a"}]}}
    new = {"id": "rs-new", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "snap-1",
                                    "reviewer_email": "zoe@x.com",
                                    "rows": [{"jobId": "b"}, {"jobId": "c"}]}}
    state = {"deleted": [], "posts": [], "fail": set(), "forced": []}

    def list_docs(kind, force=False):
        roles._READ_STATE.failed = False
        if force:
            state["forced"].append(kind)
        if kind == "shift_snapshot":
            return [snap] if "snap-1" not in state["deleted"] else []
        if kind == "reviewer_shift":
            docs = [old, new] if force else [old]
            return [d for d in docs if d["id"] not in state["deleted"]]
        if kind == "admin":
            return [{"id": "a1", "data": {"kind": "admin", "email": roles.ROOT_ADMIN_EMAIL,
                                          "name": "Root Admin"}}]
        return []

    def delete(path):
        doc_id = path.rsplit("/", 1)[-1]
        if doc_id in state["fail"]:
            resp = requests.Response()
            resp.status_code = 429
            raise requests.exceptions.HTTPError("429", response=resp)
        state["deleted"].append(doc_id)

    monkeypatch.setattr(roles, "list_docs_by_kind", list_docs)
    monkeypatch.setattr(internal_api, "delete", delete)
    monkeypatch.setattr(internal_api, "post",
                        lambda path, json=None: state["posts"].append(json) or {})
    monkeypatch.setattr(roles, "invalidate_doc_cache", lambda *a, **k: None)
    return state


def test_end_shift_deletes_assignments_the_cache_was_missing(client, storage):
    resp = client.post("/api/shifts/clear", json={"mode": "all"})
    assert resp.status_code == 200, resp.get_json()
    assert {"rs-old", "rs-new", "snap-1"} <= set(storage["deleted"])
    assert storage["deleted"][-1] == "snap-1", "the snapshot goes last"
    assert resp.get_json()["data"]["cleared_rows"] == 3


def test_a_failed_delete_keeps_the_shift(client, storage):
    storage["fail"].add("rs-new")
    resp = client.post("/api/shifts/clear", json={"mode": "all"})
    assert resp.status_code == 503
    assert "snap-1" not in storage["deleted"], "never orphan an assignment"
    assert any("didn't finish" in p["text"] for p in storage["posts"])


def test_ending_the_shift_tells_the_admin_who(client, storage):
    client.post("/api/shifts/clear", json={"mode": "all"})
    dms = [p for p in storage["posts"] if p["channel"] == main._ADMIN_SLACK_USER_ID]
    assert len(dms) == 1
    assert "Root Admin" in dms[0]["text"] and "ended the shift" in dms[0]["text"]


def test_an_unreadable_storage_deletes_nothing(client, storage, monkeypatch):
    def unreadable(kind, force=False):
        roles._READ_STATE.failed = True
        return []
    monkeypatch.setattr(roles, "list_docs_by_kind", unreadable)
    monkeypatch.setattr(main, "_latest_snapshot",
                        lambda **k: ("snap-1", {"published_at": _today_iso()}))
    resp = client.post("/api/shifts/clear", json={"mode": "all"})
    assert resp.status_code == 503
    assert storage["deleted"] == []


def test_clearing_one_reviewer_keeps_the_shift_and_sends_no_dm(client, storage):
    resp = client.post("/api/shifts/clear", json={"mode": "all", "reviewer_email": "sam@x.com"})
    assert resp.status_code == 200
    assert "snap-1" not in storage["deleted"]
    assert not any(p.get("channel") == main._ADMIN_SLACK_USER_ID for p in storage["posts"])


def test_publish_checks_for_a_running_shift_in_storage(storage):
    """The strict read publish uses must not trust the warm cache."""
    sid, _ = main._latest_snapshot(strict=True)
    assert sid == "snap-1"
    assert "shift_snapshot" in storage["forced"]


def test_display_reads_stay_on_the_warm_cache(storage):
    main._latest_snapshot()
    assert storage["forced"] == []
