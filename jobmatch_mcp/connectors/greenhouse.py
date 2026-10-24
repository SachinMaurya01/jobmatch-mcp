"""Greenhouse public job-board connector."""

from __future__ import annotations

import re
from typing import Any

import httpx

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.common import (
    clean_text,
    detect_remote,
    matches_location,
    matches_query,
    parse_iso_datetime,
)
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, SearchCriteria

_BOARD_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class GreenhouseConnector(JobConnector):
    name = "greenhouse"
    capabilities = frozenset({"query", "location", "remote_filter"})
    rate_limit_per_minute = 60

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._boards = [
            board for board in settings.greenhouse_board_tokens if _BOARD_TOKEN_RE.match(board)
        ]

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
        if not self._boards:
            return ConnectorHealth(
                name=self.name,
                ok=False,
                detail="No Greenhouse board tokens configured",
            )
        return ConnectorHealth(
            name=self.name,
            ok=True,
            detail=f"{len(self._boards)} board(s) configured (health probe not performed)",
        )

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        if not self._boards:
            raise ConfigurationError(
                "Greenhouse is not configured. Set JOBMATCH_GREENHOUSE_BOARDS."
            )

        jobs: list[RawJob] = []
        for board in self._boards:
            if len(jobs) >= criteria.limit:
                break
            payload = await self._fetch_board(board)
            for item in payload:
                parsed = self._parse_item(board, item, criteria)
                if parsed is None:
                    continue
                jobs.append(parsed)
                if len(jobs) >= criteria.limit:
                    break
        return jobs

    async def _fetch_board(self, board: str) -> list[Any]:
        url = f"{self._settings.greenhouse_base_url.rstrip('/')}/boards/{board}/jobs"
        try:
            response = await self._get_client().get(url, params={"content": "true"})
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Greenhouse request for board '{board}' failed ({type(exc).__name__})"
            ) from exc

        if response.status_code == 404:
            raise ConnectorError(f"Greenhouse board '{board}' was not found")
        if response.status_code == 429:
            raise ConnectorError("Greenhouse rate limit reached")
        if response.status_code >= 400:
            raise ConnectorError(f"Greenhouse returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("Greenhouse returned a non-JSON response") from exc

        if not isinstance(payload, dict):
            raise ConnectorError("Greenhouse returned an unexpected response")
        raw_jobs = payload.get("jobs", [])
        return raw_jobs if isinstance(raw_jobs, list) else []

    def _parse_item(
        self,
        board: str,
        item: Any,
        criteria: SearchCriteria,
    ) -> RawJob | None:
        if not isinstance(item, dict):
            return None

        external_id = item.get("id")
        title = clean_text(str(item.get("title") or ""))
        url = item.get("absolute_url")
        if not external_id or not title or not isinstance(url, str) or not url.strip():
            return None

        location_value = item.get("location")
        location = None
        if isinstance(location_value, dict):
            raw_location = location_value.get("name")
            if isinstance(raw_location, str) and raw_location.strip():
                location = clean_text(raw_location)

        company = clean_text(str(item.get("company_name") or ""))
        if not company:
            company = board.replace("-", " ").replace("_", " ").title()
        description = clean_text(str(item.get("content") or ""))
        description = description[: self._settings.max_job_description_chars]
        remote = detect_remote(title, location, description)

        if criteria.remote is True and not remote:
            return None
        if criteria.remote is False and remote:
            return None
        if not matches_query(criteria.query, title, company, description):
            return None
        if not matches_location(criteria.locations, location, remote):
            return None

        posted_at = parse_iso_datetime(item.get("first_published")) or parse_iso_datetime(
            item.get("updated_at")
        )

        return RawJob(
            source=self.name,
            external_id=f"{board}:{external_id}",
            title=title,
            company=company,
            location=location,
            remote=remote,
            salary_min=None,
            salary_max=None,
            description=description,
            url=url.strip(),
            posted_at=posted_at,
            dedupe_key=normalize_dedupe_key(title, company, location),
            sources=[self.name],
        )
