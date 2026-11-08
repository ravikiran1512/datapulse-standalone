"""Async PostgreSQL boundary: all DB access stays server-side."""
from __future__ import annotations
import os
import re
import unicodedata
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from datapulse_ingestion.models import Job
from datapulse_ingestion.matching import skill_cosine


def identity(value: str) -> str:
 value = unicodedata.normalize('NFKD', value or '')
 value = ''.join(c for c in value if not unicodedata.combining(c)).lower()
 return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', value)).strip()


def source_type_for(name: str) -> str:
 if name.lower() in {'greenhouse','lever','ashby','personio','workday','successfactors'}:
 return 'direct_ats'
 if name.lower() in {'company_portal','corporate_html','html'}:
 return 'company_portal'
 if name.lower() in {'linkedin','stepstone','xing','indeed','devjobs'}:
 return 'job_board'
 return 'aggregator'


def validate_url(value: str) -> str:
 p = urlsplit(value)
 if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None,443):
 raise ValueError('Only public HTTPS source URLs are permitted')
 if p.hostname in ('localhost','127.0.0.1','0.0.0.0') or p.hostname.endswith(('.internal','.local')):
 raise ValueError('Private source URL not accepted')
 return value


UPSERT_SQL = """
INSERT INTO datapulse.jobs (
 job_title,company_name,location,region,normalized_company_name,normalized_job_title,
 normalized_location,work_mode,employment_type,level,domain_category,language_required,
 is_english_friendly,description,tech_stack,match_score,official_apply_url,source_name,
 source_type,source_key,date_posted,date_evidence,last_seen_at,last_verified_at
) VALUES (
 $1,$2,$3,$4,$5,$6,$7,$8::datapulse.work_mode_enum,$9,
 $10::datapulse.level_enum,$11,$12,$13,$14,$15::text[],$16,$17,$18,
 $19::datapulse.source_type_enum,$20,$21,$22,now(),now()
)
ON CONFLICT (normalized_company_name,normalized_job_title,normalized_location)
DO UPDATE SET
 tech_stack=(SELECT ARRAY(SELECT DISTINCT tag FROM unnest(jobs.tech_stack || EXCLUDED.tech_stack) AS tech(tag))),
 match_score=GREATEST(jobs.match_score,EXCLUDED.match_score),
 description=CASE WHEN length(COALESCE(EXCLUDED.description,''))>length(COALESCE(jobs.description,''))
 THEN EXCLUDED.description ELSE jobs.description END,
 official_apply_url=CASE
 WHEN EXCLUDED.source_type IN ('direct_ats','company_portal') THEN EXCLUDED.official_apply_url
 WHEN jobs.source_type IN ('direct_ats','company_portal') THEN jobs.official_apply_url
 ELSE EXCLUDED.official_apply_url END,
 source_name=CASE
 WHEN EXCLUDED.source_type IN ('direct_ats','company_portal') THEN EXCLUDED.source_name
 WHEN jobs.source_type IN ('direct_ats','company_portal') THEN jobs.source_name
 ELSE EXCLUDED.source_name END,
 source_type=CASE
 WHEN EXCLUDED.source_type IN ('direct_ats','company_portal') THEN EXCLUDED.source_type
 WHEN jobs.source_type IN ('direct_ats','company_portal') THEN jobs.source_type
 ELSE EXCLUDED.source_type END,
 is_english_friendly=jobs.is_english_friendly OR EXCLUDED.is_english_friendly,
 date_posted=COALESCE(jobs.date_posted,EXCLUDED.date_posted),
 last_seen_at=now(),last_verified_at=now(),needs_review=false,is_active=true,updated_at=now()
RETURNING id
"""


async def upsert_job(pool: Any, job: Job, description: str = '') -> tuple[str, str]:
 """Stable UUID on duplicate. Preserve source sightings, never delete bookmarks."""
 stype = source_type_for(job.source_platform)
 url = validate_url(job.job_url)
 name_key = identity(job.company_name)
 title_key = identity(job.job_title)
 location_key = identity(job.location)
 if not all((name_key,title_key,location_key)):
 raise ValueError('Company, title and country-qualified location required')
 score = skill_cosine(job.tech_stack)
 params = [
 job.job_title,job.company_name,job.location,job.city,name_key,title_key,location_key,
 job.work_model if job.work_model in ('Remote','Hybrid','On-site') else 'Unknown',
 job.employment_type,job.job_level,job.domain_category,job.language_required,
 job.language_required in ('English','Bilingual'),description,job.tech_stack,score,url,
 job.source_platform,stype,job.source_key,(datetime.strptime(job.date_posted, "%Y-%m-%d").date() if job.date_posted else None),job.date_evidence,
 ]
 async with pool.acquire() as conn:
 async with conn.transaction():
 old = await conn.fetchval('''SELECT id FROM datapulse.jobs WHERE normalized_company_name=$1
 AND normalized_job_title=$2 AND normalized_location=$3''',name_key,title_key,location_key)
 record = await conn.fetchrow(UPSERT_SQL,*params)
 job_id = record['id']
 await conn.execute('''INSERT INTO datapulse.job_sources
 (job_id,source_key,source_name,source_type,apply_url,verified_at)
 VALUES ($1,$2,$3,$4::datapulse.source_type_enum,$5,now())
 ON CONFLICT (source_key) DO UPDATE SET job_id=EXCLUDED.job_id,
 apply_url=EXCLUDED.apply_url,last_seen_at=now(),verified_at=now()''',
 job_id,job.source_key,job.source_platform,stype,url)
 return ('updated' if old else 'inserted'),str(job_id)
