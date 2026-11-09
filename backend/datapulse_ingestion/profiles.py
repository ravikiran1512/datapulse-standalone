"""Independent, auditable eligibility rules for DataPulse's two job families.

Unknown experience is NOT 0 years. Geographic scope is explicit; a generic
'worldwide remote' job is not assumed available in Germany or India.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from .models import Job, RawJob
from .matching import skill_cosine
from .policy import (
    ROLE, EXCLUDE_ROLE, SENIOR, STUDENT, INTERN, JUNIOR,
    CITY_RE, DE_STATE, normalize_location, tech_tags, language,
    employment, work_model, valid_date, clean,
)

SAP_EXPERIENCED = 'sap_basis_experienced'
SAP_STUDENT = 'sap_basis_student'
DE_JUNIOR = 'data_engineering_junior'
PERSONA_IDS = (SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR)

# Deliberately match BASIS administration in the title. Generic SAP ABAP,
# SAP functional consultants and SAP reporting jobs are not BASIS positions.
SAP_BASIS_ROLE = re.compile(
    r'\b(?:sap\s+(?:basis|netweaver|hana\s+(?:admin(?:istration|istrator)?|dba)|'
    r'btp\s+(?:admin(?:istration|istrator)?|platform\s+admin)|'
    r'system\s+admin(?:istration|istrator)?|s\s*/\s*4\s*hana\s+basis)|'
    r'(?:sap\s+)?basis\s+(?:consultant|administrator|admin|engineer)|'
    r'netweaver\s+(?:administrator|consultant|engineer)|'
    r's\s*/\s*4\s*hana\s+basis|'
    r'sap\s+system\s+(?:administrator|care|operations)|'
    r'sap\s+administrator|sap\s+systembetreuung)\b', re.I,
)
SAP_SKILLS = {
    'SAP Basis': r'\bsap\s+basis\b',
    'SAP NetWeaver': r'\b(?:sap\s+)?netweaver\b',
    'SAP HANA': r'\b(?:sap\s+)?hana\b',
    'SAP S/4HANA': r'\b(?:sap\s+)?s\s*/\s*4\s*hana\b',
    'SAP Solution Manager': r'\b(?:sap\s+)?solution\s+manager\b',
    'Linux': r'\blinux\b',
    'SAP BTP': r'\b(?:sap\s+)?btp\b',
    'SAP ECC': r'\b(?:sap\s+)?ecc\b',
}
SAP_PATTERNS = {tag: re.compile(p, re.I) for tag,p in SAP_SKILLS.items()}

INDIAN_CITY_RE = re.compile(
    r'\b(?:hyderabad|bengaluru|bangalore|pune|mumbai|delhi|new\s+delhi|'
    r'noida|gurugram|gurgaon|chennai|kolkata|kochi|cochin|ahmedabad|'
    r'visakhapatnam|vizag|jaipur|bhubaneswar|indore|chandigarh|'
    r'thiruvananthapuram|trivandrum)\b', re.I,
)
DE_EXPLICIT = re.compile(r'\b(?:germany|deutschland)\b',re.I)
IN_EXPLICIT = re.compile(r'\b(?:india|bharat)\b',re.I)
REMOTE = re.compile(r'\b(?:remote|home\s*office|homeoffice|work\s+from\s+home)\b', re.I)
CANNOT_HIRE = re.compile(r'\b(?:usa|united\s+states|canada|australia|uk|united\s+kingdom|switzerland|austria|poland)\b',re.I)

# Look only for *requirements*, not tenure of an employer or perks. '3-5 years',
# 'at least 5 years', '5+ years of experience', 'experience: 5 years', etc.
EXPERIENCE_RANGE = re.compile(r'\b(\d{1,2})\s*(?:-|–|—|to|bis)\s*(\d{1,2})\s*(?:years?|yrs?|jahre[n]?)\b',re.I)
EXPERIENCE_PLUS = re.compile(r'\b(\d{1,2})\s*\+\s*(?:years?|yrs?|jahre[n]?)\b',re.I)
EXPERIENCE_MIN = re.compile(r'\b(?:at\s+least\s+|minimum\s+(?:of\s+)?|min\.?\s*|over\s+|more\s+than\s+|mindestens\s+)(\d{1,2})\s*(?:years?|yrs?|jahre[n]?)\b',re.I)
EXPERIENCE_SINGLE = re.compile(r'\b(\d{1,2})\s*(?:years?|yrs?|jahre[n]?)\s+(?:of\s+)?(?:relevant\s+|professional\s+|hands[-\s]?on\s+)?(?:experience|erfahrung|berufserfahrung|in\s+data\s+engineering|in\s+sap\s+basis)\b',re.I)
EXPERIENCE_PREFIX = re.compile(r'\b(?:experience|erfahrung|berufserfahrung)\s*[:\-]?\s*(\d{1,2})\s*(?:years?|yrs?|jahre[n]?)\b',re.I)
EXPERIENCE_REQUIREMENT = re.compile(r'\b(?:require(?:d|ments?)?|must\s+have|looking\s+for|you\s+have|bring|possess|need|minimum|mindestens|at\s+least)\b',re.I)
EXPERIENCE_CONTEXT = re.compile(r'\b(?:experience|erfahrung|berufserfahrung|professional|engineering|basis|requirements?|qualifications?|must\s+have|years?\s+in\s+the\s+(?:role|field))\b',re.I)
EXPERIENCE_BARE = re.compile(r'\b(\d{1,2})\s*(?:years?|yrs?|jahre[n]?)\b',re.I)
# Some ATS metadata labels a permanent role "entry-level / 1–2 years" while
# the employer's prose demands several years of independent professional work.
# Treat that disagreement as uncertain rather than promoting it to a student job.
EXPERIENCED_PROSE = re.compile(
    r'\b(?:mehrj[aä]hrige\w*|langj[aä]hrige\w*)\s+(?:(?:praktische|relevante|einschl[aä]gige)\s+)?(?:erfahrung|berufserfahrung)\b'
    r'|\b(?:several|multiple|many)\s+years\s+(?:of\s+)?(?:relevant\s+|professional\s+)?experience\b', re.I)

@dataclass(frozen=True)
class Experience:
    minimum: int | None
    maximum: int | None
    evidence: str | None


def parse_experience(description: str) -> Experience:
    """Extract a conservative required experience bracket, or unknown/NULL.

    Multiple requirements take the strictest lower bound; unlike an unexplained
    default of zero, this prevents an unsupported junior classification.
    """
    text = clean(re.sub(r'<[^>]*>', ' ', description or ''))
    matches: list[tuple[int,int|None,str]] = []
    spans: list[tuple[int,int]] = []
    for pattern,kind in ((EXPERIENCE_RANGE,'range'),(EXPERIENCE_PLUS,'plus'),
                         (EXPERIENCE_MIN,'min'),(EXPERIENCE_SINGLE,'single'),
                         (EXPERIENCE_PREFIX,'single'),(EXPERIENCE_BARE,'bare')):
        for m in pattern.finditer(text):
            # Do not count '6 years of experience' again inside '3–6 years'.
            if any(m.start()<end and m.end()>start for start,end in spans):
                continue
            excerpt=text[max(0,m.start()-85):min(len(text),m.end()+75)]
            # Avoid employer history e.g. 'Company founded 10 years ago'.
            if kind in ('bare',) and not EXPERIENCE_CONTEXT.search(excerpt):
                continue
            if kind not in ('single','bare') and not EXPERIENCE_CONTEXT.search(excerpt):
                continue
            if re.search(r'\b(?:benefits|history|founded|since|established|we\s+have\s+been|company\s+has)\b', excerpt, re.I) and not EXPERIENCE_REQUIREMENT.search(excerpt):
                continue
            lo=int(m.group(1))
            hi=int(m.group(2)) if kind=='range' else None
            if lo>40 or (hi is not None and (hi<lo or hi>40)):
                continue
            matches.append((lo,hi,m.group(0)))
            spans.append(m.span())
    if not matches:
        return Experience(None,None,None)
    minimum=max(x[0] for x in matches)
    maxima=[x[1] for x in matches if x[1] is not None]
    maximum=min(maxima) if maxima else None
    # Contradictory extracted ranges should not be represented as fact.
    if maximum is not None and maximum<minimum:
        return Experience(None,None,'Conflicting experience requirements; manual review needed')
    return Experience(minimum,maximum,'; '.join(dict.fromkeys(x[2] for x in matches))[:400])



def combine_structured_experience(raw: RawJob, described: Experience) -> Experience:
    """Keep explicit ATS experience metadata; description requirements take priority.

    An employer may provide both a narrative and a structured 1-2 year
    classification. Conflicting requirements are held for review, not guessed.
    """
    value=clean(getattr(raw,'structured_experience_years',None) or '')
    if not value:return described
    found=re.fullmatch(r'(\d{1,2})\s*(?:[-–—]|to|bis)\s*(\d{1,2})',value,re.I)
    if found:
        lo,hi=map(int,found.groups())
    else:
        found=re.fullmatch(r'(\d{1,2})\s*\+?',value)
        if not found:return described
        lo,hi=int(found.group(1)),None
    if lo>40 or (hi is not None and (hi<lo or hi>40)):
        return described
    structured=Experience(lo,hi,'Official ATS yearsOfExperience: '+value)
    if described.minimum is None:
        return described if described.evidence else structured
    if (hi is not None and described.minimum>hi) or (described.maximum is not None and described.maximum<lo):
        return Experience(None,None,'Conflicting description and official ATS experience')
    return Experience(max(lo,described.minimum),
        min((h for h in (hi,described.maximum) if h is not None),default=None),
        '; '.join([str(described.evidence),str(structured.evidence)]))

def country_of(raw: RawJob) -> str | None:
    location=clean(raw.location)
    code=(raw.country_code or '').strip().upper()
    de=bool(DE_EXPLICIT.search(location) or DE_STATE.search(location) or CITY_RE.search(location))
    ind=bool(IN_EXPLICIT.search(location) or INDIAN_CITY_RE.search(location))
    if code in ('DE','DEU','GERMANY'):
        return 'DE' if not ind else None
    if code in ('IN','IND','INDIA'):
        return 'IN' if not de else None
    if code:
        return None
    if de and ind:
        return None
    if de:
        return 'DE'
    if ind:
        return 'IN'
    if getattr(raw,'remote_eligible_germany',False):
        return 'DE'
    if getattr(raw,'remote_eligible_india',False):
        return 'IN'
    # Never promote worldwide/unspecified remote postings to either country.
    return None


def normalize_geo(raw: RawJob,country:str)->tuple[str,str]:
    original=clean(raw.location)
    if country=='DE':
        value=normalize_location(original, 'DE',remote_eligible_germany=raw.remote_eligible_germany)
        if value: return value
        return ('Remote (Germany)' if REMOTE.search(original) else 'Germany (Nationwide)','Germany-wide')
    city=INDIAN_CITY_RE.search(original)
    if city:
        name=city.group(0).title()
        return f'{name}, India',name
    return ('Remote (India)' if REMOTE.search(original) else 'India (Nationwide)','India-wide')


def persona_matches(raw:RawJob, country:str, exp:Experience) -> tuple[str,...]:
    title=clean(raw.job_title)
    if EXCLUDE_ROLE.search(title) or re.search(r'\b(?:director|head\s+of|vice\s+president|vp|chief|principal|staff|architect)\b',title,re.I):
        return ()
    if SAP_BASIS_ROLE.search(title):
        # Include entry through five years in both target countries. A stated
        # minimum is the eligibility threshold: a 3–6-year range includes
        # candidates with five years. Missing evidence remains NULL, not zero.
        if exp.minimum is not None and exp.minimum>5:return ()
        if exp.minimum is None and SENIOR.search(title):return ()
        contract=raw.employment_type or ''
        explicit_title=bool(STUDENT.search(title) or INTERN.search(title) or JUNIOR.search(title))
        student_contract=bool(re.search(r'\b(?:werkstudent(?:in)?|working\s+student|'
                              r'intern(?:ship)?|praktik(?:um|ant|antin)|trainee)\b',contract,re.I))
        entry_level=(str(raw.job_level or '').lower().replace('_','-') in ('entry-level','entry level','junior')
                     and (exp.minimum is None or exp.minimum<=2))
        if explicit_title or student_contract or entry_level:
            return (SAP_STUDENT,) if country in ('DE','IN') else ()
        return (SAP_EXPERIENCED,) if country in ('DE','IN') else ()
    if country not in ('DE','IN') or not ROLE.search(title):
        return ()
    if SENIOR.search(title):return ()
    # Full-time roles count too. Unknown experience is explicitly shown as
    # unverified; neither a language nor a technical match threshold excludes.
    if exp.minimum is not None and exp.minimum>2:return ()
    return (DE_JUNIOR,)


def normalize_for_profiles(raw:RawJob)->tuple[Job|None,str]:
    location_country=country_of(raw)
    if location_country is None:return None,'unverified_target_country'
    exp=combine_structured_experience(raw,parse_experience(raw.description))
    if exp.minimum is None and exp.evidence:
        return None,'ambiguous_experience_evidence'
    personas=persona_matches(raw,location_country,exp)
    contract=f'{raw.job_title} {raw.employment_type or ""}'
    if (SAP_STUDENT in personas and not (STUDENT.search(contract) or INTERN.search(contract))
            and EXPERIENCED_PROSE.search(raw.description or '')):
        return None,'experienced_prose_conflicts_with_entry_level'
    if not personas:return None,'not_eligible_for_selected_personas'
    url=urlsplit(raw.job_url or '')
    if url.scheme!='https' or not url.hostname or url.username or url.password:
        return None,'invalid_original_job_url'
    loc,city=normalize_geo(raw,location_country)
    tags=tech_tags(raw.description,raw.tech_stack)
    sap_tags=[name for name,pattern in SAP_PATTERNS.items() if pattern.search(f'{raw.job_title} {raw.description}')]
    tags=list(dict.fromkeys(tags+sap_tags))
    lang,is_english=language(raw.description)
    if DE_JUNIOR in personas:
        # The match score is for sorting and display, never an eligibility gate.
        score=skill_cosine(tags)
    else:
        # SAP technical-role relevance, not resume embedding similarity.
        score=min(.95,.75+.035*len(sap_tags))
    contract=raw.employment_type or ''
    is_working_student=bool(STUDENT.search(raw.job_title+' '+contract))
    is_intern=bool(INTERN.search(raw.job_title+' '+contract))
    is_entry_role=(str(raw.job_level or '').lower().replace('_','-') in
                   ('entry-level','entry level','junior') and
                   (exp.minimum is None or exp.minimum<=2))
    job_level='Working Student' if is_working_student else (
        'Internship' if is_intern else (
        'Junior' if JUNIOR.search(raw.job_title) or is_entry_role else 'Mid'))
    item=Job(job_title=clean(raw.job_title),company_name=clean(raw.company_name),
        location=loc,city=city,work_model=work_model(raw.location,raw.description,raw.work_model),
        employment_type=employment(raw.job_title,raw.description,raw.employment_type),
        job_level=job_level,domain_category='SAP Basis' if SAP_STUDENT in personas or SAP_EXPERIENCED in personas else 'Data Engineering',
        language_required=lang,tech_stack=tags,job_url=raw.job_url,
        source_platform=raw.source_platform,date_posted=valid_date(raw.date_posted),
        datapulse_fit_score=f'{round(score*100)}%',source_key=raw.source_key,
        date_evidence=raw.date_evidence,
        country_code=location_country,min_experience_years=exp.minimum,
        max_experience_years=exp.maximum,experience_evidence=exp.evidence,
        persona_tags=list(personas),technical_match_score=score,is_english_friendly=is_english)
    return item,'accepted'
