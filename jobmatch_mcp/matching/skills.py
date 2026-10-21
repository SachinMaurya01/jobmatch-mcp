"""Deterministic skill extraction from resumes and job descriptions."""

from __future__ import annotations

import re
from functools import cache

# canonical name -> aliases found in free text
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "Airflow": ("airflow", "apache airflow"),
    "Ansible": ("ansible",),
    "AWS": ("aws", "amazon web services"),
    "Azure": ("azure", "microsoft azure"),
    "C#": ("c#", "c sharp"),
    "C++": ("c++", "cpp"),
    "CI/CD": ("ci/cd", "ci-cd", "continuous integration"),
    "Computer Vision": ("computer vision",),
    "Django": ("django",),
    "Docker": ("docker", "containers"),
    "Elasticsearch": ("elasticsearch", "elastic search"),
    "ETL": ("etl",),
    "FastAPI": ("fastapi",),
    "Flask": ("flask",),
    "GCP": ("gcp", "google cloud platform", "google cloud"),
    "Git": ("git",),
    "Go": ("golang", "go lang"),
    "Grafana": ("grafana",),
    "GraphQL": ("graphql",),
    "gRPC": ("grpc",),
    "Hybrid Search": ("hybrid search",),
    "Java": ("java",),
    "JavaScript": ("javascript", "js"),
    "Kafka": ("kafka", "apache kafka"),
    "Kotlin": ("kotlin",),
    "Kubernetes": ("kubernetes", "k8s"),
    "LangChain": ("langchain",),
    "Linux": ("linux", "unix"),
    "LLM": (
        "llm",
        "llms",
        "large language model",
        "large language models",
        "genai",
        "generative ai",
    ),
    "Machine Learning": ("machine learning", "ml engineering"),
    "MCP": ("mcp", "model context protocol"),
    "Microservices": ("microservices", "micro-services"),
    "MLOps": ("mlops", "ml ops"),
    "MongoDB": ("mongodb", "mongo"),
    "MySQL": ("mysql",),
    "NLP": ("nlp", "natural language processing"),
    "Node.js": ("node.js", "nodejs", "node js"),
    "NumPy": ("numpy",),
    "Observability": ("observability",),
    "OpenTelemetry": ("opentelemetry", "otel"),
    "Pandas": ("pandas",),
    "PHP": ("php",),
    "PostgreSQL": ("postgresql", "postgres", "postgres db"),
    "Prometheus": ("prometheus",),
    "Prompt Engineering": ("prompt engineering",),
    "PyTorch": ("pytorch",),
    "Python": ("python", "python3"),
    "RabbitMQ": ("rabbitmq",),
    "React": ("react", "react.js", "reactjs"),
    "Redis": ("redis",),
    "REST": ("rest api", "rest apis", "restful"),
    "Rust": ("rust",),
    "scikit-learn": ("scikit-learn", "scikit learn", "sklearn"),
    "Snowflake": ("snowflake",),
    "Spark": ("spark", "apache spark", "pyspark"),
    "Spring": ("spring boot", "springboot"),
    "SQL": ("sql", "t-sql", "pl/sql"),
    "Terraform": ("terraform",),
    "TensorFlow": ("tensorflow", "tf"),
    "TypeScript": ("typescript", "ts"),
    "Vue": ("vue", "vue.js", "vuejs"),
}


@cache
def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9+#]){re.escape(alias)}(?![A-Za-z0-9+#])",
        re.IGNORECASE,
    )


def extract_skills(text: str) -> list[str]:
    """Return canonical skill names mentioned in text."""
    if not text:
        return []
    found = {
        canonical
        for canonical, aliases in SKILL_ALIASES.items()
        if any(_alias_pattern(alias).search(text) for alias in aliases)
    }
    return sorted(found)
