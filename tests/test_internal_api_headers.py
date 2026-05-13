import os
import pathlib
import datetime
import pytest
import jwt

# Ensure OIDC will fail (no metadata server locally)
os.environ.setdefault("INTERNAL_API_BASE", "http://localhost:8080")


class TestGetHeadersDevTokenFallback:
    @pytest.fixture(autouse=True)
    def setup_token_dir(self, tmp_path, monkeypatch):
        """Use a temp directory instead of ~/.storesight for tests."""
        self.token_dir = tmp_path / ".storesight"
        self.token_dir.mkdir()
        self.token_file = self.token_dir / "dev-token"
        monkeypatch.setattr(
            "internal_api._dev_token_path",
            lambda: self.token_file,
        )

    def _write_token(self, expired=False):
        now = datetime.datetime.now(datetime.timezone.utc)
        exp = now - datetime.timedelta(hours=1) if expired else now + datetime.timedelta(hours=8)
        payload = {
            "email": "dev@storesight.com",
            "aud": "storesight-dev",
            "iat": now,
            "exp": exp,
        }
        token = jwt.encode(payload, "test-secret", algorithm="HS256")
        self.token_file.write_text(token)
        return token

    def test_returns_dev_token_when_file_exists(self):
        from internal_api import _get_headers
        token = self._write_token()
        headers = _get_headers()
        assert headers == {"Authorization": f"Bearer {token}"}

    def test_returns_empty_when_no_file(self):
        from internal_api import _get_headers
        # Token file does not exist
        headers = _get_headers()
        assert headers == {}

    def test_returns_empty_when_file_is_empty(self):
        from internal_api import _get_headers
        self.token_file.write_text("")
        headers = _get_headers()
        assert headers == {}
