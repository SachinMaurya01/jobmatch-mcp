"""Guided MCP prompts for job discovery and comparison workflows."""

from __future__ import annotations


def find_jobs_for_resume(
    query: str | None = None,
    locations: str | None = None,
    remote: bool | None = None,
    min_salary: float | None = None,
) -> str:
    """Load the current resume, search for matching jobs, and summarize the top matches."""
    criteria = _criteria_text(query, locations, remote, min_salary)
    return (
        "Help me find jobs that match my resume.\n\n"
        "1. Read the `resume://current` resource to load my parsed profile. "
        "If it is missing, ask me to call `parse_resume` first.\n"
        f"2. Call `search_jobs` with these criteria: {criteria}\n"
        "3. Summarize the top 5 matches. For each one, include the score, title, "
        "company, location, remote status, salary when available, the source link, "
        "and a one-sentence reason.\n"
        "4. Point out recurring missing skills and suggest concrete resume improvements.\n"
        "5. Mention any source warnings or excluded postings from the search output.\n\n"
        "Do not invent jobs or scores. Use only the tool results."
    )


def compare_top_matches(search_id: str | None = None, count: int = 5) -> str:
    """Compare the top ranked jobs side by side and recommend the strongest fits."""
    source = (
        f"the `search://{search_id}` resource"
        if search_id
        else "the most recent `search_jobs` result"
    )
    return (
        f"Compare the top {count} jobs from {source}.\n\n"
        "Build a compact table with: rank, score, title, company, location, remote, "
        "salary, top matched skills, and the most important missing skill.\n"
        "Then recommend the two strongest fits, explain the trade-offs, and list "
        "what I could add to my resume to improve each application.\n\n"
        "Use only stored scores and explanations. Do not re-rank jobs yourself."
    )


def _criteria_text(
    query: str | None,
    locations: str | None,
    remote: bool | None,
    min_salary: float | None,
) -> str:
    parts = [f"query={query!r}" if query else "query=<infer from my resume>"]
    if locations:
        parts.append(f"locations={locations!r}")
    if remote is not None:
        parts.append(f"remote={remote}")
    if min_salary is not None:
        parts.append(f"min_salary={min_salary:g}")
    return ", ".join(parts)
