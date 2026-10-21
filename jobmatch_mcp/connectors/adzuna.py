"""Adzuna connector using the official licensed API (PRD section 7)."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, SearchCriteria

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.IGNORECASE)


def _clean_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_salary(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


class AdzunaConnector(JobConnector):
    name = "adzuna"
    capabilities = frozenset({"query", "location", "salary", "remote_filter"})

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
        if not self._settings.adzuna_configured:
            return ConnectorHealth(
                name=self.name,
                ok=False,
                detail="Adzuna credentials are not configured",
            )
        return ConnectorHealth(
            name=self.name,
            ok=True,
            detail="Credentials configured (health probe not performed)",
        )

    def _build_params(self, criteria: SearchCriteria) -> dict[str, str | int]:
        settings = self._settings
        query = criteria.query
        if criteria.remote is True:
            query = f"{query} remote" if query else "remote"

        params: dict[str, str | int] = {
            "app_id": settings.adzuna_app_id or "",
            "app_key": settings.adzuna_app_key or "",
            "results_per_page": min(
                max(criteria.limit * 3 if criteria.remote is True else criteria.limit, 1),
                50,
            ),
            "content-type": "application/json",
        }
        if query:
            params["what"] = query
        if criteria.locations:
            params["where"] = ", ".join(criteria.locations)
        if criteria.min_salary is not None and criteria.min_salary > 0:
            params["salary_min"] = int(criteria.min_salary)
        return params

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        if not self._settings.adzuna_configured:
            raise ConfigurationError(
                "Adzuna is not configured. Set JOBMATCH_ADZUNA_APP_ID and JOBMATCH_ADZUNA_APP_KEY."
            )

        url = (
            f"{self._settings.adzuna_base_url.rstrip('/')}/{self._settings.adzuna_country}/search/1"
        )
        try:
            response = await self._get_client().get(url, params=self._build_params(criteria))
        except httpx.HTTPError as exc:
            # Do not interpolate the exception: httpx includes the request URL,
            # which contains the API key in its query string.
            raise ConnectorError(f"Adzuna request failed ({type(exc).__name__})") from exc

        if response.status_code == 429:
            raise ConnectorError("Adzuna rate limit reached")
        if response.status_code >= 400:
            raise ConnectorError(f"Adzuna returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("Adzuna returned a non-JSON response") from exc

        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
        jobs: list[RawJob] = []
        for item in raw_results:
            parsed = self._parse_result(item)
            if parsed is None:
                continue
            if criteria.remote is True and not parsed.remote:
                continue
            jobs.append(parsed)
        return jobs

    def _parse_result(self, item: Any) -> RawJob | None:
        if not isinstance(item, dict):
            return None

        external_id = item.get("id")
        title = _clean_text(str(item.get("title") or ""))
        url = item.get("redirect_url")
        if not external_id or not title or not isinstance(url, str):
            return None

        company_data = item.get("company")
        company = (
            _clean_text(str(company_data.get("display_name") or ""))
            if isinstance(company_data, dict)
            else ""
        )
        location_data = item.get("location")
        location = (
            _clean_text(str(location_data.get("display_name") or ""))
            if isinstance(location_data, dict)
            else None
        )
        description = _clean_text(str(item.get("description") or ""))
        description = description[: self._settings.max_job_description_chars]

        remote = bool(_REMOTE_RE.search(f"{title} {location or ''} {description}"))

        return RawJob(
            source=self.name,
            external_id=str(external_id),
            title=title,
            company=company or "Unknown",
            location=location or None,
            remote=remote,
            salary_min=_parse_salary(item.get("salary_min")),
            salary_max=_parse_salary(item.get("salary_max")),
            description=description,
            url=url,
            posted_at=_parse_datetime(item.get("created")),
            dedupe_key=normalize_dedupe_key(title, company or "Unknown", location),
        )
