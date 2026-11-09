"""A publish that can't read Storage must refuse, not start a new shift.

A failed cold read used to come back as [] and _latest_snapshot answered "no
shift running", so publish created a brand-new snapshot over the live one —
hiding everyone else's work — and deleted the published reviewers'
completions across every shift.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402
import internal_api  # noqa: E402

ADMIN = roles.ROOT_ADMIN_EMAIL


@pytest.fixture
def client(tmp_path, monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode({"email": ADMIN, "name": "A", "iat": now,
                        "exp": now + datetime.timedelta(hours=8)}, "x", algorithm="HS256")
    d = tmp_path / ".storesight"
    d.mkdir()
    (d / "dev-token").write_text(token)
    monkeypatch.setattr("main._dev_token_path", lambda: d / "dev-token")
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def test_a_failed_scan_is_recorded_and_strict_reads_raise(monkeypatch):
    monkeypatch.setattr(roles, "_ensure_bg_started", lambda: None)
    roles._DOC_CACHE.pop("never_seen_kind", None)

    def boom(**k):
        raise RuntimeError("429")
    monkeypatch.setattr(roles, "_full_namespace_scan", boom)
    assert roles.list_docs_by_kind("never_seen_kind") == []  # lenient: unchanged
    assert roles.read_failed()
    with pytest.raises(roles.StorageReadError):
        main._read_docs("never_seen_kind", strict=True)

    monkeypatch.setattr(roles, "_full_namespace_scan", lambda **k: None)
    roles.list_docs_by_kind("never_seen_kind")
    assert not roles.read_failed(), "a later good read clears it"


def test_publish_refuses_instead_of_starting_a_new_shift(client, monkeypatch):
    writes = []
    monkeypatch.setattr(main, "_purge_stale_shift_docs", lambda: None)

    def unreadable(kind, force=False):
        roles._READ_STATE.failed = True  # what a failed cold scan records
        return []
    monkeypatch.setattr(roles, "list_docs_by_kind", unreadable)
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    for name in ("post", "put", "delete"):
        monkeypatch.setattr(internal_api, name, lambda *a, _n=name, **k: writes.append(_n) or {"data": {"id": "x"}})

    resp = client.post("/api/shifts/publish", json={"assignments": {
        "sam@storesight.com": [{"id": "1", "jobId": "1", "projectId": "P1",
                                "name": "Shelf", "unreviewedCount": 5}]}})
    assert resp.status_code == 503, resp.get_json()
    assert "nothing was published" in resp.get_json()["error"]
    assert writes == [], "no snapshot, shift doc or completion delete may happen"
