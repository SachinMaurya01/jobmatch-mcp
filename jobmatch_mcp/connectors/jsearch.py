"""JSearch connector using the RapidAPI-hosted API (PRD section 7)."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
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

_PERIOD_MULTIPLIERS: dict[str, float] = {
    "HOUR": 2080.0,
    "DAY": 260.0,
    "WEEK": 52.0,
    "MONTH": 12.0,
    "YEAR": 1.0,
}


def _clean_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _parse_salary(value: Any, period: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    multiplier = _PERIOD_MULTIPLIERS.get(str(period or "YEAR").upper(), 1.0)
    return float(value) * multiplier


def _build_location(item: dict[str, Any]) -> str | None:
    explicit = item.get("job_location")
    if isinstance(explicit, str) and explicit.strip():
        return _clean_text(explicit)

    parts = [
        _clean_text(str(item.get(key)))
        for key in ("job_city", "job_state", "job_country")
        if isinstance(item.get(key), str) and str(item.get(key)).strip()
    ]
    location = ", ".join(dict.fromkeys(parts))
    return location or None


class JSearchConnector(JobConnector):
    name = "jsearch"
    capabilities = frozenset({"query", "location", "country", "remote_filter", "salary_filter"})

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
        if not self._settings.jsearch_configured:
            return ConnectorHealth(
                name=self.name,
                ok=False,
                detail="JSearch API key is not configured",
            )
        return ConnectorHealth(
            name=self.name,
            ok=True,
            detail="API key configured (health probe not performed)",
        )

    def _build_query(self, criteria: SearchCriteria) -> str:
        parts: list[str] = []
        if criteria.query:
            parts.append(criteria.query.strip())
        if criteria.locations:
            parts.append(f"in {', '.join(criteria.locations)}")
        return " ".join(part for part in parts if part).strip() or "jobs"

    def _build_params(self, criteria: SearchCriteria) -> dict[str, str | int]:
        pages = max(1, min((criteria.limit + 9) // 10, 20))
        params: dict[str, str | int] = {
            "query": self._build_query(criteria),
            "num_pages": pages,
            "country": self._settings.jsearch_country,
        }
        if criteria.remote is True:
            params["work_from_home"] = "true"
        return params

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        if not self._settings.jsearch_configured:
            raise ConfigurationError("JSearch is not configured. Set JOBMATCH_JSEARCH_API_KEY.")

        url = f"{self._settings.jsearch_base_url.rstrip('/')}/search"
        headers = {
            "X-RapidAPI-Key": self._settings.jsearch_api_key or "",
            "X-RapidAPI-Host": self._settings.jsearch_api_host,
        }
        try:
            response = await self._get_client().get(
                url,
                params=self._build_params(criteria),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            # Never interpolate the exception: httpx can include request details.
            raise ConnectorError(f"JSearch request failed ({type(exc).__name__})") from exc

        if response.status_code == 401:
            raise ConnectorError("JSearch authentication failed; check the RapidAPI key")
        if response.status_code == 403:
            raise ConnectorError(
                "JSearch access forbidden; check the RapidAPI plan and key permissions"
            )
        if response.status_code == 429:
            raise ConnectorError("JSearch rate limit reached")
        if response.status_code >= 400:
            raise ConnectorError(f"JSearch returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("JSearch returned a non-JSON response") from exc

        if not isinstance(payload, dict):
            raise ConnectorError("JSearch returned an unexpected response")
        status = payload.get("status")
        if isinstance(status, str) and status.upper() != "OK":
            raise ConnectorError(f"JSearch returned status {status[:40]}")

        raw_results = payload.get("data", [])
        if not isinstance(raw_results, list):
            raw_results = []

        jobs: list[RawJob] = []
        for item in raw_results:
            parsed = self._parse_result(item)
            if parsed is None:
                continue
            if criteria.remote is True and not parsed.remote:
                continue
            if criteria.remote is False and parsed.remote:
                continue
            if criteria.min_salary is not None and criteria.min_salary > 0:
                best_known = parsed.salary_max or parsed.salary_min
                if best_known is not None and best_known < criteria.min_salary:
                    continue
            jobs.append(parsed)
        return jobs

    def _parse_result(self, item: Any) -> RawJob | None:
        if not isinstance(item, dict):
            return None

        external_id = item.get("job_id")
        title = _clean_text(str(item.get("job_title") or ""))
        url = item.get("job_apply_link") or item.get("job_google_link")
        if not external_id or not title or not isinstance(url, str) or not url.strip():
            return None

        company = _clean_text(str(item.get("employer_name") or "")) or "Unknown"
        location = _build_location(item)
        description = _clean_text(str(item.get("job_description") or ""))
        description = description[: self._settings.max_job_description_chars]

        remote_value = item.get("job_is_remote")
        remote = (
            bool(remote_value)
            if isinstance(remote_value, bool)
            else bool(_REMOTE_RE.search(f"{title} {location or ''} {description}"))
        )

        period = item.get("job_salary_period")
        posted_at = _parse_datetime(item.get("job_posted_at_datetime_utc")) or _parse_timestamp(
            item.get("job_posted_at_timestamp")
        )

        return RawJob(
            source=self.name,
            external_id=str(external_id),
            title=title,
            company=company,
            location=location,
            remote=remote,
            salary_min=_parse_salary(item.get("job_min_salary"), period),
            salary_max=_parse_salary(item.get("job_max_salary"), period),
            description=description,
            url=url.strip(),
            posted_at=posted_at,
            dedupe_key=normalize_dedupe_key(title, company, location),
        )
