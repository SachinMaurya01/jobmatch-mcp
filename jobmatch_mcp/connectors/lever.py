"""Lever public postings connector."""

from __future__ import annotations

import re
from typing import Any

import httpx

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.common import (
    clean_text,
    detect_remote,
    lever_annual_salary,
    matches_location,
    matches_query,
    parse_epoch,
)
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, SearchCriteria

_COMPANY_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class LeverConnector(JobConnector):
    name = "lever"
    capabilities = frozenset({"query", "location", "remote_filter", "salary_filter"})
    rate_limit_per_minute = 60

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._companies = [
            company for company in settings.lever_company_tokens if _COMPANY_TOKEN_RE.match(company)
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
        if not self._companies:
            return ConnectorHealth(
                name=self.name,
                ok=False,
                detail="No Lever company tokens configured",
            )
        return ConnectorHealth(
            name=self.name,
            ok=True,
            detail=f"{len(self._companies)} company feed(s) configured (probe not performed)",
        )

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        if not self._companies:
            raise ConfigurationError("Lever is not configured. Set JOBMATCH_LEVER_COMPANIES.")

        jobs: list[RawJob] = []
        for company in self._companies:
            if len(jobs) >= criteria.limit:
                break
            payload = await self._fetch_company(company)
            for item in payload:
                parsed = self._parse_item(company, item, criteria)
                if parsed is None:
                    continue
                jobs.append(parsed)
                if len(jobs) >= criteria.limit:
                    break
        return jobs

    async def _fetch_company(self, company: str) -> list[Any]:
        url = f"{self._settings.lever_base_url.rstrip('/')}/postings/{company}"
        try:
            response = await self._get_client().get(url, params={"mode": "json"})
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Lever request for '{company}' failed ({type(exc).__name__})"
            ) from exc

        if response.status_code == 404:
            raise ConnectorError(f"Lever company '{company}' was not found")
        if response.status_code == 429:
            raise ConnectorError("Lever rate limit reached")
        if response.status_code >= 400:
            raise ConnectorError(f"Lever returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError("Lever returned a non-JSON response") from exc

        if isinstance(payload, dict):
            postings = payload.get("data", [])
            return postings if isinstance(postings, list) else []
        return payload if isinstance(payload, list) else []

    def _parse_item(
        self,
        company_token: str,
        item: Any,
        criteria: SearchCriteria,
    ) -> RawJob | None:
        if not isinstance(item, dict):
            return None

        external_id = item.get("id")
        title = clean_text(str(item.get("text") or ""))
        url = item.get("hostedUrl") or item.get("applyUrl")
        if not external_id or not title or not isinstance(url, str) or not url.strip():
            return None

        categories = item.get("categories")
        category_data = categories if isinstance(categories, dict) else {}
        location = self._build_location(category_data)
        company = company_token.replace("-", " ").replace("_", " ").title()

        description = self._build_description(item)
        description = description[: self._settings.max_job_description_chars]

        workplace = item.get("workplaceType")
        remote = (isinstance(workplace, str) and workplace.lower() == "remote") or detect_remote(
            title, location, description
        )

        if criteria.remote is True and not remote:
            return None
        if criteria.remote is False and remote:
            return None
        if not matches_query(criteria.query, title, company, description):
            return None
        if not matches_location(criteria.locations, location, remote):
            return None

        salary_min, salary_max = self._build_salary(item)
        if criteria.min_salary is not None and criteria.min_salary > 0:
            best_known = salary_max or salary_min
            if best_known is not None and best_known < criteria.min_salary:
                return None

        return RawJob(
            source=self.name,
            external_id=f"{company_token}:{external_id}",
            title=title,
            company=company,
            location=location,
            remote=remote,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            url=url.strip(),
            posted_at=parse_epoch(item.get("createdAt"), milliseconds=True),
            dedupe_key=normalize_dedupe_key(title, company, location),
            sources=[self.name],
        )

    @staticmethod
    def _build_location(categories: dict[str, Any]) -> str | None:
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            values = [
                clean_text(str(value))
                for value in all_locations
                if isinstance(value, str) and value.strip()
            ]
            if values:
                return ", ".join(dict.fromkeys(values))
        location = categories.get("location")
        if isinstance(location, str) and location.strip():
            return clean_text(location)
        return None

    @staticmethod
    def _build_description(item: dict[str, Any]) -> str:
        parts: list[str] = []
        plain = item.get("descriptionPlain")
        if isinstance(plain, str) and plain.strip():
            parts.append(clean_text(plain))
        elif isinstance(item.get("description"), str):
            parts.append(clean_text(item["description"]))

        lists = item.get("lists")
        if isinstance(lists, list):
            for entry in lists:
                if not isinstance(entry, dict):
                    continue
                heading = entry.get("text")
                content = entry.get("content")
                if isinstance(heading, str) and heading.strip():
                    parts.append(clean_text(heading))
                if isinstance(content, str) and content.strip():
                    parts.append(clean_text(content))

        additional = item.get("additionalPlain")
        if isinstance(additional, str) and additional.strip():
            parts.append(clean_text(additional))
        return " ".join(part for part in parts if part)

    @staticmethod
    def _build_salary(item: dict[str, Any]) -> tuple[float | None, float | None]:
        salary_range = item.get("salaryRange")
        if not isinstance(salary_range, dict):
            return None, None
        interval = salary_range.get("interval")
        return (
            lever_annual_salary(salary_range.get("min"), interval),
            lever_annual_salary(salary_range.get("max"), interval),
        )
