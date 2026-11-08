"""Source-independent write-time policy. Every collector and the API use it."""
from __future__ import annotations
import re
from dataclasses import dataclass
from .models import Job
from .policy import ROLE, EXCLUDE_ROLE, SENIOR, tech_tags, normalize_location
from .matching import CORE, skill_cosine

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str
    match_score: float


def validate_for_write(job: Job) -> ValidationResult:
    score = skill_cosine(job.tech_stack)
    if not ROLE.search(job.job_title) or EXCLUDE_ROLE.search(job.job_title):
        return ValidationResult(False, "excluded_or_non_engineering_title", score)
    if SENIOR.search(job.job_title) or job.job_level not in ("Working Student", "Internship", "Junior", "Mid"):
        return ValidationResult(False, "excluded_seniority", score)
    if not normalize_location(job.location):
        return ValidationResult(False, "not_germany_eligible", score)
    tags = set(tech_tags("", job.tech_stack))
    if len(tags) < 3:
        return ValidationResult(False, "fewer_than_three_tech_tags", score)
    if len(tags & CORE) < 2:
        return ValidationResult(False, "insufficient_core_de_skills", score)
    if job.language_required not in ("English", "Bilingual"):
        return ValidationResult(False, "english_unverified", score)
    if score < .70:
        return ValidationResult(False, "below_70_percent_cosine", score)
    return ValidationResult(True, "accepted", score)
