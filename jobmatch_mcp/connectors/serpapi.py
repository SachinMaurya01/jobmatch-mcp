"""Google Jobs connector backed by SerpAPI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.common import (
    clean_text,
    detect_remote,
    matches_location,
    parse_iso_datetime,
)
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, SearchCriteria


class SerpApiConnector(JobConnector):
    name = "serpapi"
    capabilities = frozenset({"query", "location", "remote_filter"})
    rate_limit_per_minute = 30

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.http_timeout_seconds,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> ConnectorHealth:
        if not self._settings.serpapi_configured:
            return ConnectorHealth(
                name=self.name,
                ok=False,
                detail="SerpAPI key is not configured",
            )
        return ConnectorHealth(
            name=self.name,
            ok=True,
            detail="API key configured (health probe not performed)",
        )

    def _build_params(self, criteria: SearchCriteria) -> dict[str, str]:
        query = (criteria.query or "jobs").strip() or "jobs"
        params = {
            "engine": "google_jobs",
            "q": query,
            "gl": self._settings.serpapi_country,
            "hl": "en",
            "api_key": self._settings.serpapi_api_key or "",
        }
        if criteria.locations:
            params["location"] = criteria.locations[0]
        return params

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        if not self._settings.serpapi_configured:
            raise ConfigurationError("SerpAPI is not configured. Set JOBMATCH_SERPAPI_API_KEY.")

        url = f"{self._settings.serpapi_base_url.rstrip('/')}/search.json"
        try:
            response = await self._get_client().get(url, params=self._build_params(criteria))
        except httpx.HTTPError as exc:
            raise ConnectorError(f"SerpAPI request failed ({type(exc).__name__})") from exc

        if response.status_code == 401:
            raise ConnectorError("SerpAPI authentication failed; check the API key")
        if response.status_code == 429:
            raise ConnectorError("SerpAPI rate limit reached")
        if response.status_code >= 400:
            raise ConnectorError(f"SerpAPI returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("SerpAPI returned a non-JSON response") from exc

        if not isinstance(payload, dict):
            raise ConnectorError("SerpAPI returned an unexpected response")
        if payload.get("error"):
            raise ConnectorError(f"SerpAPI returned an error: {str(payload['error'])[:200]}")

        raw_results = payload.get("jobs_results", [])
        if not isinstance(raw_results, list):
            return []

        jobs: list[RawJob] = []
        for item in raw_results:
            parsed = self._parse_item(item, criteria)
            if parsed is None:
                continue
            jobs.append(parsed)
            if len(jobs) >= criteria.limit:
                break
        return jobs

    def _parse_item(self, item: Any, criteria: SearchCriteria) -> RawJob | None:
        if not isinstance(item, dict):
            return None

        external_id = item.get("job_id")
        title = clean_text(str(item.get("title") or ""))
        url = self._pick_url(item)
        if not external_id or not title or url is None:
            return None

        company = clean_text(str(item.get("company_name") or "")) or "Unknown"
        location_value = item.get("location")
        location = (
            clean_text(location_value)
            if isinstance(location_value, str) and location_value.strip()
            else None
        )
        description = clean_text(str(item.get("description") or ""))
        description = description[: self._settings.max_job_description_chars]
        remote = detect_remote(title, location, description)

        if criteria.remote is True and not remote:
            return None
        if criteria.remote is False and remote:
            return None
        if not matches_location(criteria.locations, location, remote):
            return None

        return RawJob(
            source=self.name,
            external_id=str(external_id),
            title=title,
            company=company,
            location=location,
            remote=remote,
            salary_min=None,
            salary_max=None,
            description=description,
            url=url,
            posted_at=self._parse_posted_at(item),
            dedupe_key=normalize_dedupe_key(title, company, location),
            sources=[self.name],
        )

    @staticmethod
    def _pick_url(item: dict[str, Any]) -> str | None:
        apply_options = item.get("apply_options")
        if isinstance(apply_options, list):
            for option in apply_options:
                if not isinstance(option, dict):
                    continue
                link = option.get("link")
                if isinstance(link, str) and link.strip():
                    return link.strip()
        share_link = item.get("share_link")
        if isinstance(share_link, str) and share_link.strip():
            return share_link.strip()
        return None

    @staticmethod
    def _parse_posted_at(item: dict[str, Any]) -> datetime | None:
        detected = item.get("detected_extensions")
        if isinstance(detected, dict):
            return parse_iso_datetime(detected.get("posted_at"))
        return None
