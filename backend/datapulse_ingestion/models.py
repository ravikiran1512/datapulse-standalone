from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RawJob:
    source_platform: str
    source_id: str
    company_name: str
    job_title: str
    location: str
    job_url: str
    description: str = ""
    date_posted: str | None = None
    date_evidence: str | None = None
    work_model: str | None = None
    employment_type: str | None = None
    job_level: str | None = None
    country_code: str | None = None
    remote_eligible_germany: bool = False
    tech_stack: list[str] = field(default_factory=list)

    @property
    def source_key(self) -> str:
        return f"{self.source_platform}:{self.source_id}"


@dataclass
class Job:
    job_title: str
    company_name: str
    location: str
    city: str
    work_model: str
    employment_type: str
    job_level: str
    domain_category: str
    language_required: str
    tech_stack: list[str]
    job_url: str
    source_platform: str
    date_posted: str | None
    datapulse_fit_score: str
    source_key: str
    date_evidence: str | None = None

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        # Internal dedup/provenance fields are not in the externally requested payload.
        data.pop("city")
        data.pop("source_key")
        data.pop("date_evidence")
        return data
