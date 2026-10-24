"""Cosine similarity between resume and posting embeddings."""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def semantic_component(
    resume_embedding: Sequence[float] | None,
    job_embedding: Sequence[float] | None,
    *,
    resume_model: str | None,
    job_model: str | None,
    expected_model: str | None,
) -> tuple[float | None, str]:
    if resume_embedding is None or job_embedding is None:
        return None, "Semantic similarity unavailable; weight redistributed."
    if expected_model is None or resume_model != expected_model or job_model != expected_model:
        return None, "Embedding models differ from the active index; weight redistributed."
    score = cosine_similarity(resume_embedding, job_embedding)
    return score, f"Semantic similarity {score * 100:.1f}/100 using {expected_model}."
