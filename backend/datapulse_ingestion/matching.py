"""Deterministic actual cosine similarity over a transparent skill feature space.

This is not a semantic embedding or hiring-probability estimate. Each technology
is an independent feature, with optional related-family features for PySpark,
Databricks, Azure, etc. Cosine output is in [0, 1].
"""
from __future__ import annotations
import math
from .policy import tech_tags

RESUME = ["Python", "SQL", "PySpark", "Apache Spark", "Databricks", "Delta Lake", "Azure", "ADLS Gen2", "SAP"]
CORE = {"Python", "SQL", "PySpark", "Apache Spark", "Databricks", "Hadoop", "SAP"}
# Technical alignment scores more heavily when Python, SQL, Spark, Databricks overlap.
WEIGHTS = {"Python": 2., "SQL": 2., "PySpark": 2., "Databricks": 2., "Apache Spark": 1.4,
 "Delta Lake": 1.1, "Azure": 1.2, "ADLS Gen2": 1.2, "Hadoop": .8, "SAP": .9}


def _vector(tags: list[str]) -> dict[str, float]:
 recognized = set(tech_tags("", tags))
 v = {tag: WEIGHTS.get(tag, .6) for tag in recognized}
 # Related technologies share a small and explicit data-platform feature.
 if recognized.intersection({"PySpark", "Apache Spark", "Databricks", "Hadoop"}):
 v["family:spark"] = 1.5
 if recognized.intersection({"Azure", "ADLS Gen2", "Databricks"}):
 v["family:lakehouse"] = 1.0
 return v


def skill_cosine(tags: list[str]) -> float:
 lhs = _vector(RESUME)
 rhs = _vector(tags)
 if not rhs:
 return 0.0
 dot = sum(a * rhs.get(k, 0.0) for k, a in lhs.items())
 magnitude = math.sqrt(sum(v*v for v in lhs.values()) * sum(v*v for v in rhs.values()))
 return round(min(1.0, max(0.0, dot / magnitude)), 6) if magnitude else 0.0
