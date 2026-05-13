"""Thin wrapper for calling the Internal Tool API with automatic Cloud Run auth."""

import os
import logging
import pathlib
import time

import requests
import google.auth.transport.requests
import google.oauth2.id_token

INTERNAL_API_BASE = os.environ.get("INTERNAL_API_BASE", "https://internal-tool-api.storesight.org")
TOOL_SLUG = os.environ.get("TOOL_SLUG", "")
LOCAL_DEV = os.environ.get("LOCAL_DEV") == "1"

_auth_request = google.auth.transport.requests.Request()


def _dev_token_path():
    """Return the path to the dev token file."""
    return pathlib.Path.home() / ".storesight" / "dev-token"


def _get_headers():
    """Return auth headers. On Cloud Run the metadata server provides an OIDC
    identity token for the service account. Locally, falls back to the dev
    token at ~/.storesight/dev-token if it exists."""
    # In local dev mode, skip OIDC (avoids metadata server timeout)
    if not LOCAL_DEV:
        try:
            token = google.oauth2.id_token.fetch_id_token(_auth_request, INTERNAL_API_BASE)
            headers = {"Authorization": f"Bearer {token}"}
            if TOOL_SLUG:
                headers["X-Tool-Slug"] = TOOL_SLUG
            return headers
        except Exception:
            pass

    # Fall back to dev token file
    token_file = _dev_token_path()
    try:
        token = token_file.read_text().strip()
        if token:
            headers = {"Authorization": f"Bearer {token}"}
            if TOOL_SLUG:
                headers["X-Tool-Slug"] = TOOL_SLUG
            return headers
    except FileNotFoundError:
        pass

    logging.debug("No identity token or dev token available")
    headers = {}
    if TOOL_SLUG:
        headers["X-Tool-Slug"] = TOOL_SLUG
    return headers


def _ensure_trailing_slash(path):
    """Ensure path ends with / to avoid Flask 308 redirects that strip auth headers."""
    if not path.endswith("/"):
        path += "/"
    return path


_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 2, 4]  # seconds to wait between retries on 429


def _request(method, path, **kwargs):
    """Send a request with automatic retry on 429 (rate limit).

    The API enforces per-tool rate limits (60 requests/minute by default,
    keyed on the X-Tool-Slug header). If you hit the limit, this helper
    waits and retries up to 3 times with exponential backoff.

    Default timeout is 60s because some list endpoints (notably /api/jobs)
    routinely take 30+ seconds per page.
    """
    url = f"{INTERNAL_API_BASE}{_ensure_trailing_slash(path)}"
    kwargs.setdefault("headers", _get_headers())
    kwargs.setdefault("timeout", 120)

    for attempt in range(_MAX_RETRIES + 1):
        resp = requests.request(method, url, **kwargs)
        if resp.status_code != 429 or attempt == _MAX_RETRIES:
            resp.raise_for_status()
            return resp.json()
        wait = _RETRY_BACKOFF[attempt]
        logging.warning(
            "Rate limited (429) on %s %s — retrying in %ds (attempt %d/%d)",
            method.upper(), path, wait, attempt + 1, _MAX_RETRIES,
        )
        time.sleep(wait)


def get(path, params=None):
    """GET a path on the Internal API. Returns the parsed JSON response.

    Usage:
        from internal_api import get
        data = get("/api/agents", params={"page": 1})
    """
    return _request("GET", path, params=params)


def post(path, json=None):
    """POST to the Internal API. Returns the parsed JSON response.

    Usage:
        from internal_api import post
        result = post("/api/slack/post", json={"channel": "C01ABCDEF", "text": "Hello!"})
    """
    return _request("POST", path, json=json)


def put(path, json=None):
    """PUT to the Internal API. Returns the parsed JSON response.

    Usage:
        from internal_api import put
        result = put("/api/storage/my-tool/abc123", json={"data": {"name": "updated"}})
    """
    return _request("PUT", path, json=json)


def delete(path):
    """DELETE on the Internal API. Returns the parsed JSON response.

    Usage:
        from internal_api import delete
        result = delete("/api/storage/my-tool/abc123")
    """
    return _request("DELETE", path)
