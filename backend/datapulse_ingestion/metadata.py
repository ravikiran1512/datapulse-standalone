"""Conservative evidence-based extraction from an employer's vacancy text.

Unknown is NOT false evidence of being English-friendly. Relocation assistance
is not necessarily visa sponsorship. All values are informational, not guarantees.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

from .policy import language

VISA = re.compile(r'\b(?:visa\s+(?:sponsorship|support|assistance)|sponsor(?:ing|ed)?\s+(?:your\s+)?(?:visa|work\s+permit)|blue\s+card\s+(?:sponsorship|support)|work\s+permit\s+sponsorship)\b',re.I)
VISA_NEGATED = re.compile(r'\b(?:no|not|without|do\s+not|cannot|unable\s+to)\s+(?:provide\s+|offer\s+)?(?:visa\s+sponsorship|sponsor\s+(?:visas?|work\s+permits?))\b',re.I)
RELOCATION = re.compile(r'\b(?:relocation\s+(?:assistance|support|package)|we\s+(?:cover|pay)\s+(?:your\s+)?relocation)\b',re.I)
# Employer-provided salary in EUR: capture amount with € or EUR and a range marker.
EUR_RANGE = re.compile(r'(?:€\s*|EUR\s*)(\d{2,3}(?:[.,]\d{3})?\s*[kK]?)\s*[-–—to]{1,4}\s*(?:(?:€|EUR)\s*)?(\d{2,3}(?:[.,]\d{3})?\s*[kK]?)',re.I)
INR_LPA = re.compile(r'(?:₹|INR|Rs\.?|\b)\s*(\d{1,3}(?:\.\d+)?)\s*[-–—]\s*(?:₹|INR|Rs\.?)?\s*(\d{1,3}(?:\.\d+)?)\s*(?:LPA|lakhs?\s+per\s+annum)\b',re.I)

@dataclass(frozen=True)
class CandidateMetadata:
    english_friendly: bool
    visa_supported: bool
    relocation_assistance: bool
    salary_range: str | None
    salary_currency: str | None
    salary_evidence: str | None


def extract_metadata(description: str) -> CandidateMetadata:
    text = re.sub(r'<[^>]+>', ' ', description or '')
    _, english = language(text)
    # `not German C1 required` is not proof that English is an accepted language.
    visa = bool(VISA.search(text)) and not bool(VISA_NEGATED.search(text))
    relocate = bool(RELOCATION.search(text))
    salary = currency = evidence = None
    m = EUR_RANGE.search(text)
    if m:
        def val(v: str) -> int:
            v=v.strip().lower().replace(' ','')
            if v.endswith('k'): return int(float(v[:-1].replace(',','.'))*1000)
            return int(v.replace('.','').replace(',',''))
        lo,hi=val(m.group(1)),val(m.group(2))
        if 10000<=lo<=hi<=400000:
            salary=f'€{lo:,}–€{hi:,}';currency='EUR';evidence=m.group(0)[:110]
    if salary is None:
        m=INR_LPA.search(text)
        if m:
            lo,hi=float(m.group(1)),float(m.group(2))
            if .5<=lo<=hi<=300:
                salary=f'₹{lo:g}–₹{hi:g} LPA';currency='INR';evidence=m.group(0)[:110]
    return CandidateMetadata(english,visa,relocate,salary,currency,evidence)


def match_breakdown(domain: str,tags:list[str],personas:list[str],minimum:int|None,maximum:int|None)->dict:
    expected=(['SAP Basis','SAP NetWeaver','SAP HANA','SAP S/4HANA','Linux','SAP BTP']
              if domain=='SAP Basis' else ['Python','SQL','PySpark','Databricks','Apache Spark','Delta Lake','dbt','Airflow'])
    actual=set(tags)
    if 'data_engineering_junior' in personas:
        fit='Targets 0–2 years' if minimum is None or minimum<=2 else 'Review requirement'
    elif 'sap_basis_experienced' in personas:
        fit='Targets approximately 3–6 years'
    elif 'sap_basis_student' in personas:
        fit='Student / entry-level preset; contract type still matters'
    else:
        fit='Profile eligibility not established'
    return {'matched_skills':[x for x in expected if x in actual],
            'missing_or_recommended_skills':[x for x in expected if x not in actual],
            'experience_fit':fit,
            'method':'Profile-based comparison; not a personal resume assessment'}
