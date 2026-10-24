"""Cross-source deduplication behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from jobmatch_mcp.matching.dedupe import dedupe_jobs, normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob


def make_job(
    *,
    source: str,
    external_id: str,
    title: str = "Senior Python Engineer",
    company: str = "Acme Inc",
    location: str | None = "Bengaluru, India",
    remote: bool = False,
    salary_min: float | None = None,
    salary_max: float | None = None,
    description: str = "Short description.",
    posted_at: datetime | None = None,
) -> RawJob:
    return RawJob(
        source=source,
        external_id=external_id,
        title=title,
        company=company,
        location=location,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
        description=description,
        url=f"https://example.com/{source}/{external_id}",
        posted_at=posted_at,
        dedupe_key=normalize_dedupe_key(title, company, location),
        sources=[source],
    )


def test_normalizes_company_legal_suffixes() -> None:
    assert normalize_dedupe_key("Engineer", "Acme Inc.", "Delhi") == normalize_dedupe_key(
        "Engineer", "Acme Pvt Ltd", "Delhi"
    )


def test_merges_duplicate_postings_across_sources() -> None:
    first = make_job(
        source="jsearch",
        external_id="a",
        description="A much longer description with Python, AWS, Docker and FastAPI.",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    second = make_job(
        source="greenhouse",
        external_id="b",
        company="Acme",
        remote=True,
        salary_min=120_000,
        salary_max=150_000,
        description="Short.",
        posted_at=datetime(2026, 10, 5, tzinfo=UTC),
    )

    merged = dedupe_jobs([first, second])

    assert len(merged) == 1
    job = merged[0]
    assert job.source == "jsearch"
    assert job.external_id == "a"
    assert job.sources == ["greenhouse", "jsearch"]
    assert job.remote is True
    assert job.salary_min == 120_000
    assert job.salary_max == 150_000
    assert job.description == first.description
    assert job.posted_at == first.posted_at


def test_keeps_distinct_postings() -> None:
    first = make_job(source="jsearch", external_id="a")
    second = make_job(source="lever", external_id="b", title="Data Engineer")
    assert len(dedupe_jobs([first, second])) == 2


def test_preserves_single_source_attribution() -> None:
    job = make_job(source="lever", external_id="a")
    merged = dedupe_jobs([job])
    assert merged[0].sources == ["lever"]
