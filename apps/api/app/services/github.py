"""GitHub App client: app JWT -> installation tokens -> repos and code snapshots.

Docs: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
"""

import time
from collections.abc import Callable

import httpx
import jwt

from app.core.config import settings

API = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GitHubAppClient:
    def __init__(self, app_id: int, private_key_pem: str,
                 transport: httpx.BaseTransport | None = None, timeout: float = 60.0):
        self.app_id = app_id
        self._key = private_key_pem
        self._http = httpx.Client(
            base_url=API,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "flagpilot"},
            timeout=timeout, transport=transport, follow_redirects=True,
        )
        self._tokens: dict[str, tuple[str, float]] = {}

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- auth ------------------------------------------------------------

    def app_jwt(self) -> str:
        now = int(time.time())
        # iat backdated 60s for clock drift; GitHub allows at most 10 minutes of validity.
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)},
                          self._key, algorithm="RS256")

    def installation_token(self, installation_id: str) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        data = self._request("POST", f"/app/installations/{installation_id}/access_tokens",
                             auth=f"Bearer {self.app_jwt()}")
        self._tokens[installation_id] = (data["token"], time.time() + 3000)  # tokens live 1h
        return data["token"]

    # --- low level -------------------------------------------------------

    def _request(self, method: str, url: str, auth: str, **kwargs):
        try:
            r = self._http.request(method, url, headers={"Authorization": auth}, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not reach GitHub: {exc}") from exc
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            raise GitHubError(f"GitHub returned {r.status_code}: {msg[:300]}", r.status_code)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.content

    def _paginate(self, url: str, auth: str, key: str | None = None, max_pages: int = 50) -> list:
        items, page = [], 1
        while page <= max_pages:
            data = self._request("GET", url, auth, params={"per_page": 100, "page": page})
            batch = data[key] if key else data
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1
        return items

    # --- endpoints -------------------------------------------------------

    def list_installations(self) -> list[dict]:
        return self._paginate("/app/installations", f"Bearer {self.app_jwt()}")

    def list_installation_repos(self, installation_id: str) -> list[dict]:
        tok = self.installation_token(installation_id)
        return self._paginate("/installation/repositories", f"token {tok}", key="repositories")

    def branch_head_sha(self, installation_id: str, full_name: str, branch: str) -> str:
        tok = self.installation_token(installation_id)
        return self._request("GET", f"/repos/{full_name}/commits/{branch}", f"token {tok}")["sha"]

    def download_tarball(self, installation_id: str, full_name: str, ref: str) -> bytes:
        tok = self.installation_token(installation_id)
        data = self._request("GET", f"/repos/{full_name}/tarball/{ref}", f"token {tok}")
        if not isinstance(data, bytes):
            raise GitHubError("Unexpected tarball response from GitHub")
        return data


def default_client() -> GitHubAppClient:
    if not (settings.github_app_id and settings.github_private_key_pem):
        raise GitHubError("GitHub App is not configured (GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY)")
    return GitHubAppClient(settings.github_app_id, settings.github_private_key_pem)


GitHubClientFactory = Callable[[], GitHubAppClient]


def get_github_client_factory() -> GitHubClientFactory:
    """FastAPI dependency; tests override it to inject a fake GitHub."""
    return default_client
