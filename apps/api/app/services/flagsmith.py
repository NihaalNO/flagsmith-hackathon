"""Thin client for the Flagsmith Management (Admin) API.

Auth: Organisation API key, sent as `Authorization: Api-Key <key>`.
Docs: https://docs.flagsmith.com/integrating-with-flagsmith/flagsmith-api-overview/admin-api
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx

from app.core.security import decrypt_secret
from app.models.integration import Integration

DEFAULT_BASE_URL = "https://api.flagsmith.com/api/v1"
MAX_PAGES = 100  # safety stop for pagination loops


class FlagsmithError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FlagsmithClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Api-Key {api_key}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- low level -------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> Any:
        try:
            r = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise FlagsmithError(f"Could not reach Flagsmith: {exc}") from exc
        if r.status_code == 401:
            raise FlagsmithError("Flagsmith rejected the API key (check key and permissions)", r.status_code)
        if r.status_code == 403:
            raise FlagsmithError(
                f"The Flagsmith API key isn't allowed to {method} {url.split('?')[0]} "
                "(make it an admin key or grant the permission in Flagsmith)", r.status_code)
        if r.status_code >= 400:
            raise FlagsmithError(f"Flagsmith returned {r.status_code}: {r.text[:300]}", r.status_code)
        return r.json() if r.content else None

    def _paginate(self, url: str, params: dict | None = None) -> Iterator[dict]:
        """Yield items from either a plain list or a {results, next} paginated response."""
        data = self._request("GET", url, params=params)
        for _ in range(MAX_PAGES):
            if isinstance(data, list):
                yield from data
                return
            yield from data.get("results", [])
            next_url = data.get("next")
            if not next_url:
                return
            data = self._request("GET", next_url)  # absolute URL; httpx accepts it
        raise FlagsmithError("Too many pages returned by Flagsmith")

    # --- endpoints -------------------------------------------------------

    def list_organisations(self) -> list[dict]:
        return list(self._paginate("organisations/"))

    def list_projects(self) -> list[dict]:
        return list(self._paginate("projects/"))

    def create_project(self, name: str, organisation_id: int) -> dict:
        """Flagsmith also creates Development and Production environments for it."""
        return self._request("POST", "projects/", json={"name": name, "organisation": organisation_id})

    def list_environments(self, project_id: int) -> list[dict]:
        return list(self._paginate("environments/", {"project": project_id}))

    def list_features(self, project_id: int, environment_id: int | None = None) -> list[dict]:
        """Features of a project. With `environment_id` (numeric id), each feature also carries its
        live state there: `environment_feature_state`, `num_segment_overrides`,
        `num_identity_overrides` (null on Edge projects), `last_modified_in_current_environment`."""
        params: dict = {"page_size": 100}
        if environment_id is not None:
            params["environment"] = environment_id
        return list(self._paginate(f"projects/{project_id}/features/", params))

    def list_tags(self, project_id: int) -> dict[int, str]:
        """Tag id -> label for a project."""
        return {int(t["id"]): t.get("label") or str(t["id"])
                for t in self._paginate(f"projects/{project_id}/tags/") if t.get("id") is not None}

    def create_feature(
        self, project_id: int, name: str, description: str | None = None, default_enabled: bool = False
    ) -> dict:
        body = {"name": name, "default_enabled": default_enabled}
        if description:
            body["description"] = description
        return self._request("POST", f"projects/{project_id}/features/", json=body)

    def list_environment_feature_states(self, environment_api_key: str) -> dict[int, bool]:
        """Map feature id -> enabled for the environment (environment-level states only)."""
        states: dict[int, bool] = {}
        for fs in self._paginate(f"environments/{environment_api_key}/featurestates/"):
            # Skip identity / segment overrides; we want the environment default.
            if fs.get("identity") or fs.get("feature_segment"):
                continue
            feature = fs.get("feature")
            feature_id = feature.get("id") if isinstance(feature, dict) else feature
            if feature_id is not None:
                states[int(feature_id)] = bool(fs.get("enabled"))
        return states


def client_for_integration(integration: Integration) -> FlagsmithClient:
    if integration.provider != "flagsmith":
        raise FlagsmithError("Integration is not a Flagsmith integration")
    if not integration.encrypted_secret:
        raise FlagsmithError("Integration has no API key; set `api_token` first")
    base_url = (integration.config or {}).get("base_url") or DEFAULT_BASE_URL
    return FlagsmithClient(decrypt_secret(integration.encrypted_secret), base_url=base_url)


ClientFactory = Callable[[Integration], FlagsmithClient]


def get_flagsmith_client_factory() -> ClientFactory:
    """FastAPI dependency; tests override it to inject a fake Flagsmith."""
    return client_for_integration
