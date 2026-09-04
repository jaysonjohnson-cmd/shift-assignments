"""Yesterday's shift must not linger in today's My Tasks.

Shifts run inside one working day (8:00 AM at the earliest, 6:00 PM at the
latest), so a snapshot published on an earlier local day is finished. The read
paths hide those; a publish also reclaims the storage they held.

Day boundaries are US Central — the QC team is in Arkansas — so a shift
published at 4 PM CDT (stored 21:00Z) still counts as that day.
"""

import datetime
import os
from zoneinfo import ZoneInfo

import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402

CENTRAL = ZoneInfo("America/Chicago")


def _iso_days_ago(days, hour=12):
    d = datetime.datetime.now(CENTRAL).replace(
        hour=hour, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days)
    return d.astimezone(datetime.timezone.utc).isoformat()


# --- the date helper ---------------------------------------------------------

def test_local_date_uses_central_not_utc():
    """A late-afternoon Central shift must not roll into the next UTC day."""
    # 2026-06-15 16:00 CDT == 2026-06-15 21:00Z — same day either way.
    assert main._local_shift_date("2026-06-15T21:00:00+00:00") == datetime.date(2026, 6, 15)
    # 2026-06-15 20:00 CDT == 2026-06-16 01:00Z — UTC says the 16th, Central the 15th.
    assert main._local_shift_date("2026-06-16T01:00:00+00:00") == datetime.date(2026, 6, 15)


def test_unparseable_timestamp_is_treated_as_current():
    """Failing to read a date must not hide a live shift."""
    assert main._local_shift_date("not-a-date") is None
    assert main._is_stale_snapshot({"published_at": "not-a-date"}) is False
    assert main._is_stale_snapshot({}) is False


def test_today_is_not_stale_yesterday_is():
    assert main._is_stale_snapshot({"published_at": _iso_days_ago(0)}) is False
    assert main._is_stale_snapshot({"published_at": _iso_days_ago(1)}) is True


def test_a_shift_published_this_morning_survives_all_day():
    """An 8 AM publish must still be live at 5 PM the same day."""
    assert main._is_stale_snapshot({"published_at": _iso_days_ago(0, hour=8)}) is False


# --- _latest_snapshot --------------------------------------------------------

def _snap(sid, published_at):
    return {"id": sid, "data": {"kind": "shift_snapshot", "published_at": published_at}}


def _shift(sid, rows=1):
    return {"id": f"rs-{sid}", "data": {"kind": "reviewer_shift",
                                        "shift_snapshot_id": sid,
                                        "rows": [{"jobId": f"j{i}"} for i in range(rows)]}}


@pytest.fixture
def snapshots(monkeypatch):
    def setup(snaps, shifts):
        monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
            "shift_snapshot": snaps, "reviewer_shift": shifts,
        }.get(kind, []))
    return setup


def test_yesterdays_shift_is_hidden(snapshots):
    snapshots([_snap("old", _iso_days_ago(1))], [_shift("old")])
    assert main._latest_snapshot() == (None, None)


def test_todays_shift_is_returned(snapshots):
    snapshots([_snap("new", _iso_days_ago(0))], [_shift("new")])
    sid, _ = main._latest_snapshot()
    assert sid == "new"


def test_include_stale_still_reaches_a_finished_shift(snapshots):
    """Clearing up after the fact must be able to see yesterday's shift."""
    snapshots([_snap("old", _iso_days_ago(1))], [_shift("old")])
    sid, _ = main._latest_snapshot(include_stale=True)
    assert sid == "old", "admin clear would otherwise have nothing to act on"


def test_todays_shift_wins_over_an_older_one(snapshots):
    snapshots([_snap("today", _iso_days_ago(0)), _snap("old", _iso_days_ago(3))],
              [_shift("today"), _shift("old")])
    sid, _ = main._latest_snapshot()
    assert sid == "today"


# --- storage reclamation -----------------------------------------------------

def test_purge_deletes_only_stale_docs(monkeypatch):
    """Yesterday's docs go; today's stay. Storage caps at 10k per namespace."""
    deleted = []
    snaps = [_snap("old", _iso_days_ago(2)), _snap("today", _iso_days_ago(0))]
    shifts = [_shift("old", rows=3), _shift("today", rows=2)]
    comps = [
        {"id": "c-old", "data": {"kind": "completion", "shift_snapshot_id": "old"}},
        {"id": "c-new", "data": {"kind": "completion", "shift_snapshot_id": "today"}},
    ]
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
        "shift_snapshot": snaps, "reviewer_shift": shifts, "completion": comps,
    }.get(kind, []))
    monkeypatch.setattr(main, "_try_delete", lambda did: deleted.append(did))
    monkeypatch.setattr(main.roles, "invalidate_doc_cache", lambda *a, **k: None)

    n_snaps, n_rows, n_comps = main._purge_stale_shift_docs()

    assert (n_snaps, n_rows, n_comps) == (1, 3, 1)
    assert set(deleted) == {"old", "rs-old", "c-old"}
    assert "today" not in deleted and "rs-today" not in deleted and "c-new" not in deleted


def test_purge_is_a_noop_when_nothing_is_stale(monkeypatch):
    """No stale snapshot means no scans and no deletes."""
    deleted = []
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
        "shift_snapshot": [_snap("today", _iso_days_ago(0))],
        "reviewer_shift": [_shift("today")],
    }.get(kind, []))
    monkeypatch.setattr(main, "_try_delete", lambda did: deleted.append(did))

    assert main._purge_stale_shift_docs() == (0, 0, 0)
    assert deleted == []
