"""Tests for the Cloud Scheduler OIDC path on the auth middleware.

Production auth is a storesight_session cookie. The one exception is the shift
auto-publish path, which Cloud Scheduler calls on a timer with a Google OIDC
token. These tests pin the exception narrow: right path, right service account,
verified token, nothing else.
"""

import os

import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402

SCHED_SA = "qc-scheduler@storesight-internal-tools.iam.gserviceaccount.com"
AUDIENCE = "https://qc-shift-assignments.storesight.org"


@pytest.fixture
def prod(monkeypatch):
    """Run the middleware in its production branch with OIDC configured."""
    monkeypatch.setattr(main, "LOCAL_DEV", False)
    monkeypatch.setattr(main, "_OIDC_SERVICE_ACCOUNTS", frozenset({SCHED_SA}))
    monkeypatch.setattr(main, "_OIDC_AUDIENCE", AUDIENCE)
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def _stub_verify(monkeypatch, claims):
    """Stand in for Google's verifier; raise to simulate an invalid token."""
    import google.oauth2.id_token as ga_id_token

    def fake(token, request, audience=None):
        if isinstance(claims, Exception):
            raise claims
        assert audience == AUDIENCE, "audience must always be verified"
        return claims

    monkeypatch.setattr(ga_id_token, "verify_oauth2_token", fake)


def _valid_claims(email=SCHED_SA):
    return {"iss": "https://accounts.google.com", "email": email,
            "email_verified": True, "aud": AUDIENCE}


def test_allowlisted_service_account_is_accepted(prod, monkeypatch):
    _stub_verify(monkeypatch, _valid_claims())
    # Toggle is off by default, so the endpoint short-circuits — a 200 with
    # "skipped" proves we got past auth, which is what's under test.
    monkeypatch.setattr(main, "_get_auto_publish_enabled", lambda *a, **k: False)

    resp = prod.post("/api/shifts/auto-publish", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    assert resp.get_json()["skipped"] is True


def test_unknown_service_account_is_refused(prod, monkeypatch):
    _stub_verify(monkeypatch, _valid_claims(email="attacker@evil.example"))
    resp = prod.post("/api/shifts/auto-publish", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 302, "must fall through to the login redirect"


def test_invalid_token_is_refused(prod, monkeypatch):
    _stub_verify(monkeypatch, ValueError("signature mismatch"))
    resp = prod.post("/api/shifts/auto-publish", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 302


def test_unverified_email_is_refused(prod, monkeypatch):
    claims = _valid_claims()
    claims["email_verified"] = False
    _stub_verify(monkeypatch, claims)
    resp = prod.post("/api/shifts/auto-publish", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 302


def test_wrong_issuer_is_refused(prod, monkeypatch):
    claims = _valid_claims()
    claims["iss"] = "https://evil.example"
    _stub_verify(monkeypatch, claims)
    resp = prod.post("/api/shifts/auto-publish", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 302


def test_oidc_does_not_open_any_other_endpoint(prod, monkeypatch):
    """The allowlist is the whole point — a valid token must not unlock the API."""
    _stub_verify(monkeypatch, _valid_claims())
    for path in ("/api/me", "/api/reviewers", "/api/shifts/my", "/api/admins"):
        resp = prod.get(path, headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 302, f"{path} must still require the cookie"


def test_oidc_is_off_unless_both_settings_are_present(monkeypatch):
    """Half-configured must mean disabled, never 'skip audience verification'."""
    monkeypatch.setattr(main, "LOCAL_DEV", False)
    _stub_verify(monkeypatch, _valid_claims())

    monkeypatch.setattr(main, "_OIDC_SERVICE_ACCOUNTS", frozenset({SCHED_SA}))
    monkeypatch.setattr(main, "_OIDC_AUDIENCE", "")
    with main.app.test_client() as c:
        assert c.post("/api/shifts/auto-publish",
                      headers={"Authorization": "Bearer tok"}).status_code == 302

    monkeypatch.setattr(main, "_OIDC_SERVICE_ACCOUNTS", frozenset())
    monkeypatch.setattr(main, "_OIDC_AUDIENCE", AUDIENCE)
    with main.app.test_client() as c:
        assert c.post("/api/shifts/auto-publish",
                      headers={"Authorization": "Bearer tok"}).status_code == 302


def test_no_bearer_header_still_redirects(prod):
    resp = prod.post("/api/shifts/auto-publish")
    assert resp.status_code == 302
