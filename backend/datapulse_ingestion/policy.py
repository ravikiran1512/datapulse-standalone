from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from .models import Job, RawJob

ROLE = re.compile(
    r"\b(?:data\s+(?:engineer(?:ing)?|platform\s+engineer|integration\s+engineer)|"
    r"big\s+data\s+(?:engineer|developer)|(?:py\s*spark|pyspark)\s+engineer|"
    r"(?:etl|elt|etl/elt)\s+(?:engineer|developer)|"
    r"analytics\s+engineer|cloud\s+data\s+engineer|"
    r"(?:ai|genai|ai/data)\s+data\s+engineer|"
    r"data\s+engineer\s+(?:for|\+|&)\s+ai|"
    r"sap\s+data\s+integration(?:\s+(?:engineer|developer|consultant))?|"
    r"dateningenieur(?:in)?|datenplattform[-\s]?entwickler)\b",
    re.I,
)
EXCLUDE_ROLE = re.compile(
    r"\b(?:business\s+analyst|data\s+entry|dashboard\s+(?:specialist|analyst)|"
    r"bi\s+(?:analyst|reporting)|ai\s+policy|ai\s+strateg(?:y|ist)|"
    r"ph\.?d\.?|doctoral|postdoc(?:toral)?|research\s+fellow|"
    r"principal|staff\s+(?:engineer|developer)|director|head\s+of|"
    r"lead\s+architect|senior\s+architect)\b", re.I,
)
SENIOR = re.compile(r"\b(?:senior|sr\.|lead|chief|principal|staff|director|head)\b", re.I)
# A title without "Senior" can still require senior-level experience.
# Only explicit experience requirements of 7+ years are a hard exclusion.
SENIOR_YEARS_REQUIRED = re.compile(
    r"\\b(?:at\\s+least|minimum(?:\\s+of)?|over|more\\s+than)\\s+(?:[7-9]|[1-9]\\d)\\s*\\+?\\s*(?:years?|yrs?)\\b"
    r"|\\b(?:[7-9]|[1-9]\\d)\\s*\\+\\s*(?:years?|yrs?)\\s+(?:of|in|working|professional|experience)\\b",
    re.I,
)
STUDENT = re.compile(r"\b(?:werkstudent(?:in)?|working\s+student|student\s+assistant|studentische\s+hilfskraft)\b", re.I)
INTERN = re.compile(r"\b(?:intern(?:ship)?|praktik(?:ant|antin|um)|trainee)\b", re.I)
JUNIOR = re.compile(r"\b(?:junior|entry[-\s]?level|graduate|berufseinstieg|einsteiger)\b", re.I)

# Separate, explicit technology names. Families/case-normalization are deterministic.
TECH_PATTERNS = {
    "Python": r"\bpython\b", "SQL": r"\bsql\b", "PySpark": r"\bpy\s*spark\b",
    "Apache Spark": r"\b(?:apache\s+)?spark\b", "Scala": r"\bscala\b", "Rust": r"\brust\b",
    "Databricks": r"\bdatabricks\b", "Delta Lake": r"\bdelta\s+lake\b", "Snowflake": r"\bsnowflake\b",
    "AWS": r"\b(?:aws|amazon\s+web\s+services)\b", "S3": r"\b(?:amazon\s+)?s3\b",
    "Redshift": r"\bredshift\b", "Glue": r"\b(?:aws\s+)?glue\b", "EMR": r"\b(?:aws\s+)?emr\b",
    "Azure": r"\b(?:microsoft\s+)?azure\b", "ADF": r"\b(?:adf|azure\s+data\s+factory)\b",
    "Synapse": r"\b(?:azure\s+)?synapse\b", "ADLS Gen2": r"\b(?:adls(?:\s+gen\s*2)?|azure\s+data\s+lake(?:\s+storage)?)\b",
    "GCP": r"\b(?:gcp|google\s+cloud(?:\s+platform)?)\b", "BigQuery": r"\bbig\s*query\b",
    "dbt": r"\bdbt\b", "Airflow": r"\b(?:apache\s+)?airflow\b", "Kafka": r"\bkafka\b",
    "HDFS": r"\bhdfs\b", "Hadoop": r"\bhadoop\b", "SAP": r"\bsap\b",
}
COMPILED_TECH = {name: re.compile(pattern, re.I) for name, pattern in TECH_PATTERNS.items()}
RESUME_WEIGHTS = {
    "Python": 18, "SQL": 18, "PySpark": 18, "Apache Spark": 12, "Databricks": 18,
    "Delta Lake": 10, "Azure": 11, "ADLS Gen2": 11, "dbt": 1,
}
WEIGHTS = {t: 8 for t in TECH_PATTERNS}
WEIGHTS.update({"Python":18,"SQL":18,"PySpark":20,"Apache Spark":12,"Databricks":20,"Delta Lake":12,"Azure":11,"ADLS Gen2":11})
ENGLISH = re.compile(r"\b(?:english|englisch(?:kenntnisse)?)\b", re.I)
GERMAN = re.compile(r"\b(?:german|deutsch(?:kenntnisse|e)?|deutschsprachig)\b", re.I)
GERMAN_MANDATORY = re.compile(
    r"(?:\b(?:german|deutsch\w*)\b.{0,45}\b(?:b2|c1|c2|fluent|fließend|fliessend|verhandlungssicher|mandatory|erforderlich|required|muttersprachlich)\b|"
    r"\b(?:b2|c1|c2|fluent|fließend|fliessend|verhandlungssicher|mandatory|required)\b.{0,30}\b(?:german|deutsch\w*)\b)", re.I,
)
DE_STATE = re.compile(r"\b(?:bayern|bavaria|baden[-\s]?württemberg|hessen|hesse|sachsen[-\s]?anhalt|saxony[-\s]?anhalt|sachsen|saxony|niedersachsen|lower saxony|nordrhein[-\s]?westfalen|north rhine[-\s]?westphalia|brandenburg|thüringen|thuringia|rheinland[-\s]?pfalz|rhineland[-\s]?palatinate|schleswig[-\s]?holstein|mecklenburg[-\s]?vorpommern|saarland|berlin|hamburg|bremen)\b", re.I)
CITIES = "berlin hamburg munich münchen magdeburg frankfurt cologne köln stuttgart düsseldorf düsseldorf dortmund essen bonn bochum duisburg wuppertal bielefeld münster karlsruhe mannheim heidelberg freiburg augsburg ingolstadt nürnberg nuremberg erlangen regensburg würzburg ulm aachen leipzig dresden chemnitz halle potsdam rostock schwerin kiel lübeck flensburg hannover hanover braunschweig wolfsburg göttingen oldenburg osnabrück bremen darmstadt mainz wiesbaden kassel offenbach trier koblenz saarbrücken paderborn siegen solingen leverkusen lüneburg göttingen konstanz jena erfurt weimar magdeburg dessau stendal".split()
# Explicit list as a fallback; structured DE country codes always override this limited list.
CITY_RE = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in sorted(set(CITIES),key=len,reverse=True)) + r")\b",re.I)
BAD_COUNTRIES = re.compile(r"\b(?:usa|united states|united kingdom|canada|australia|india|switzerland|schweiz|austria|österreich|netherlands|nederland|france|frankreich|spain|españa|poland|polska)\b",re.I)


def clean(s: str) -> str:
    return re.sub(r"\s+"," ",s or "").strip()


def tech_tags(description: str, listed: list[str] | None = None) -> list[str]:
    text = " ".join([description or "", *(listed or [])])
    matches = [n for n, regex in COMPILED_TECH.items() if regex.search(text)]
    return matches


def normalize_location(location: str, country_code: str | None = None, *, remote_eligible_germany=False) -> tuple[str, str] | None:
    original = clean(location)
    country = (country_code or "").upper()
    if country and country not in ("DE", "DEU", "GERMANY"):
        return None
    if country not in ("DE","DEU","GERMANY"):
        if BAD_COUNTRIES.search(original) and not re.search(r"\b(?:germany|deutschland)\b",original,re.I):
            return None
        if not (re.search(r"\b(?:germany|deutschland)\b",original,re.I) or DE_STATE.search(original) or CITY_RE.search(original) or re.search(r"\bD[- ]?\d{5}\b",original) or remote_eligible_germany):
            return None
    city_match = CITY_RE.search(original)
    raw_city = city_match.group().lower() if city_match else "Germany-wide"
    alias = {"münchen":"Munich","köln":"Cologne","nürnberg":"Nuremberg", "hannover":"Hanover"}
    city = "Germany-wide" if raw_city == "Germany-wide" else alias.get(raw_city, raw_city.title())
    if re.search(r"\bremote\b|home\s*office|homeoffice|von\s+zuhause",original,re.I) and city == "Germany-wide":
        return ("Remote (Germany)", "Germany-wide")
    if city == "Germany-wide":
        return ("Germany (Nationwide)", "Germany-wide")
    return (f"{city}, Germany",city)


def level(title: str, explicit: str | None = None) -> str:
    text = f"{title} {explicit or ''}"
    if STUDENT.search(text): return "Working Student"
    if INTERN.search(text): return "Internship"
    if JUNIOR.search(text): return "Junior"
    if SENIOR.search(text): return "Senior"
    # Unqualified engineer positions are tentatively Mid; score alone is not a qualification assertion.
    return "Mid"


def language(text: str) -> tuple[str, bool]:
    en = bool(ENGLISH.search(text))
    de = bool(GERMAN.search(text))
    de_hard = bool(GERMAN_MANDATORY.search(text) or re.search(
        r"\\b(?:very\\s+good|excellent|strong|advanced|fluent|professional)\\s+(?:written\\s+and\\s+spoken\\s+)?(?:german|deutsch\\w*)\\b"
        r"|\\b(?:sehr\\s+gute|ausgezeichnete|flie\\u00dfende|verhandlungssichere)\\s+deutsch\\w*\\b",
        text, re.I,
    ))
    if en and de: return "Bilingual",not de_hard
    if de: return "German",False
    if en: return "English",True
    return "Unknown",False


def employment(title: str, desc: str, explicit: str | None = None) -> str:
    t=clean(f"{title} {explicit or ''}")
    if STUDENT.search(t): return "Werkstudent"
    if INTERN.search(t): return "Internship"
    if re.search(r"\b(?:part[- ]?time|teilzeit)\b",t,re.I):return "Part-time"
    if re.search(r"\b(?:full[- ]?time|vollzeit)\b",t,re.I):return "Full-time"
    return "Unknown" # avoid inventing a full-time condition.


def work_model(location: str, desc: str, explicit: str | None = None) -> str:
    text=clean(f"{location} {explicit or ''}")
    if re.search(r"\bhybrid\b|hybridmodell",text,re.I):return "Hybrid"
    if re.search(r"\bremote\b|home[- ]?office|homeoffice|fully\s+remote",text,re.I):return "Remote"
    if re.search(r"\b(?:on[- ]?site|onsite|vor\s+ort|office[- ]?based)\b",text,re.I):return "On-site"
    return "Unknown" # do not invent an unconfirmed work model.


def role_domain(title: str) -> str:
    if re.search(r"(?:ai|genai|mlops|machine learning)",title,re.I):return "AI Engineering"
    return "Data Engineering"


def fit_score(tags: list[str]) -> int:
    """Technical relevance only, not an employer ATS probability."""
    weighted_all = sum(WEIGHTS[t] for t in tags)
    if not weighted_all: return 0
    known = set(tags) & set(RESUME_WEIGHTS)
    coverage = sum(WEIGHTS[t] for t in known) / weighted_all
    core_count = len(known & {"Python","SQL","PySpark","Apache Spark","Databricks","Delta Lake","Azure","ADLS Gen2"})
    depth = min(1,core_count/4)
    return max(0,min(100,round(100*(.65*coverage+.35*depth))))


def valid_date(raw: str | None) -> str | None:
    if not raw: return None
    try:
        value=str(raw)[:10]
        date.fromisoformat(value)
        # Reject future dates; do not promote 'updated_at' to posted_at.
        return value if value <= date.today().isoformat() else None
    except (TypeError,ValueError):return None


def normalize_job(raw: RawJob, criteria: dict) -> tuple[Job | None, str]:
    filters=criteria.get("filters",{})
    title=clean(raw.job_title)
    if not ROLE.search(title): return None,"not_target_engineering_title"
    if EXCLUDE_ROLE.search(title):return None,"hard_excluded_role"
    if filters.get("exclude_senior",True) and (SENIOR.search(title) or SENIOR_YEARS_REQUIRED.search(raw.description)):\n        return None,"above_target_level"
    for word in filters.get("exclude_keywords",[]):
        if re.search(r"(?<!\w)"+re.escape(word)+r"(?!\w)",title,re.I): return None,"excluded_title_keyword"
    loc=normalize_location(raw.location,raw.country_code,remote_eligible_germany=raw.remote_eligible_germany)
    if not loc: return None,"not_confirmed_germany_eligible"
    tags=tech_tags(raw.description,raw.tech_stack)
    if len(tags)<int(filters.get("min_distinct_tech_tags",3)):return None,"fewer_than_three_technologies"
    lang,english_ok=language(raw.description)
    if filters.get("english_friendly_only",True) and not english_ok:
        if not (lang=="Unknown" and filters.get("allow_unknown_language",False)):
            return None,"english_requirement_unverified_or_german_mandatory"
    lvl=level(title,raw.job_level)
    if lvl not in filters.get("target_levels",["Working Student","Internship","Junior","Mid"]):
        return None,"level_not_in_target"
    score=fit_score(tags)
    if score/100 < float(filters.get("min_resume_match_score",.7)):
        return None,"below_resume_match_threshold"
    u=urlsplit(raw.job_url)
    if u.scheme!="https" or not u.hostname or u.username or u.password:
        return None,"invalid_original_job_url"
    posted=valid_date(raw.date_posted)
    date_evidence=raw.date_evidence if posted else "unverified/missing exact posting date"
    item=Job(job_title=title,company_name=clean(raw.company_name),location=loc[0],city=loc[1],
        work_model=work_model(raw.location,raw.description,raw.work_model),
        employment_type=employment(title,raw.description,raw.employment_type),job_level=lvl,
        domain_category=role_domain(title),language_required=lang,tech_stack=tags,
        job_url=raw.job_url,source_platform=raw.source_platform,date_posted=posted,
        datapulse_fit_score=f"{score}%",source_key=raw.source_key,date_evidence=date_evidence)
    return item,"accepted"
