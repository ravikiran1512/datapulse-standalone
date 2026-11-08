"""DataPulse v2 — independent FastAPI/asyncpg service.
All write operations are server-side; CORS does not grant write permissions.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Literal
import hmac
import json
import os
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import normalize_for_profiles, PERSONA_IDS, SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR
from .db import upsert_job, source_type_for
from datapulse_ingestion.metadata import match_breakdown
from .search import boolean_condition, SearchSyntaxError


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool=None
    dsn=os.getenv('DATABASE_URL')
    if dsn:
        import asyncpg
        app.state.pool=await asyncpg.create_pool(dsn=dsn,min_size=1,max_size=5,command_timeout=25)
    try:yield
    finally:
        if app.state.pool:await app.state.pool.close()


app=FastAPI(title='DataPulse Germany Jobs API',version='2.0.0',lifespan=lifespan)
origins=[x.strip() for x in os.getenv('FRONTEND_ORIGINS','http://localhost:3000').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,
                   allow_methods=['GET','POST','DELETE','PATCH'],allow_headers=['Authorization','Content-Type'])


def db(request:Request):
    pool=getattr(request.app.state,'pool',None)
    if pool is None:raise HTTPException(503,'DATABASE_URL is not configured')
    return pool


class JobResponse(BaseModel):
    id:str
    job_title:str
    company_name:str
    location:str
    region:str|None
    country_code:str|None=None
    country:str|None=None
    min_experience_years:int|None=None
    max_experience_years:int|None=None
    experience_evidence:str|None=None
    persona_tags:list[str]=Field(default_factory=list)
    visa_supported:bool=False
    relocation_assistance:bool=False
    salary_range:str|None=None
    salary_currency:str|None=None
    link_status:str='unchecked'
    last_link_checked_at:datetime|None=None
    fit_breakdown:dict=Field(default_factory=dict)
    work_mode:str
    employment_type:str
    level:str
    domain_category:str
    language_required:str
    is_english_friendly:bool
    description:str|None=None
    tech_stack:list[str]=Field(default_factory=list)
    match_score:float
    official_apply_url:str
    source_name:str
    source_type:str
    date_posted:date|None=None
    last_verified_at:datetime|None=None
    created_at:datetime


class SearchPage(BaseModel):
    items:list[JobResponse]
    total:int
    page:int
    limit:int
    scanned_at:datetime|None=None


@app.get('/api/v1/health')
async def health(request:Request):
    if getattr(request.app.state,'pool',None) is None:
        return {'status':'setup_required','database_connected':False}
    try:
        await request.app.state.pool.fetchval('SELECT 1')
        return {'status':'ok','database_connected':True}
    except Exception:
        raise HTTPException(503,'Database unavailable')


@app.get('/api/v1/jobs',response_model=SearchPage)
async def jobs(
    request:Request,
    query:str|None=None,
    persona:list[Literal['sap_basis_experienced','sap_basis_student','data_engineering_junior']]=Query(default=[]),
    country:Literal['DE','IN']|None=None,
    region:str|None=None,
    work_mode:Literal['Remote','Hybrid','On-site']|None=None,
    level:Literal['Working Student','Internship','Junior','Mid']|None=None,
    role_type:Literal['SAP Basis','Data Engineering','AI Engineering','MLOps']|None=None,
    skills:list[str]=Query(default=[]),
    english_only:bool=False,
    min_match_score:float=Query(default=0,ge=0,le=1),
    page:int=Query(default=1,ge=1),
    limit:int=Query(default=20,ge=1,le=100),
    order:Literal['newest','match']='newest',
):
    pool=db(request)
    params=[]
    conditions=['is_active=TRUE','needs_review=FALSE']
    # Combined personas use OR: each preset retains its own country + experience
    # constraints. Never treat a NULL experience as 0 years.
    selections=persona or list(PERSONA_IDS)
    params.append(selections)
    profiles=f'''(
      ('sap_basis_experienced'=ANY($1::text[]) AND 'sap_basis_experienced'=ANY(persona_tags)
        AND country_code IN ('DE','IN') AND min_experience_years BETWEEN 3 AND 6
        AND (max_experience_years IS NULL OR max_experience_years <= 6))
      OR ('sap_basis_student'=ANY($1::text[]) AND 'sap_basis_student'=ANY(persona_tags)
        AND country_code='DE' AND level IN ('Working Student','Internship','Junior'))
      OR ('data_engineering_junior'=ANY($1::text[]) AND 'data_engineering_junior'=ANY(persona_tags)
        AND country_code='DE' AND level IN ('Working Student','Internship','Junior')
        AND (min_experience_years IS NULL OR min_experience_years<=2)
        AND (max_experience_years IS NULL OR max_experience_years<=2))
    )'''
    conditions.append(profiles)
    if country:
        params.append(country)
        conditions.append(f'country_code=${len(params)}')

    try:conditions.append(boolean_condition(query,params))
    except SearchSyntaxError as exc:raise HTTPException(422,str(exc)) from exc
    if region and region.lower() not in ('all','germany','nationwide'):
        params.append(f'%{region.lower()}%')
        conditions.append(f'(lower(location) LIKE ${len(params)} OR lower(coalesce(region,\'\')) LIKE ${len(params)})')
    if work_mode:
        params.append(work_mode);conditions.append(f'work_mode=${len(params)}::datapulse.work_mode_enum')
    if level:
        params.append(level);conditions.append(f'level=${len(params)}::datapulse.level_enum')
    if role_type:
        params.append(role_type);conditions.append(f'domain_category=${len(params)}')
    if english_only:conditions.append('is_english_friendly=TRUE')
    params.append(min_match_score);conditions.append(f'match_score >= ${len(params)}')
    if skills:
        params.append(skills[:20]);conditions.append(f'tech_stack && ${len(params)}::text[]')
    where=' AND '.join(conditions)
    total_sql=f'SELECT count(*) FROM datapulse.jobs WHERE {where}'
    sorting=('match_score DESC, date_posted DESC NULLS LAST, first_seen_at DESC' if order=='match'
             else 'date_posted DESC NULLS LAST, first_seen_at DESC, match_score DESC')
    params.extend([limit,(page-1)*limit])
    sql=f'''SELECT id::text,job_title,company_name,location,region,work_mode::text,employment_type,
      level::text,domain_category,language_required,is_english_friendly,description,tech_stack,
      match_score,official_apply_url,source_name,source_type::text,date_posted,last_verified_at,created_at,
      country_code,country,min_experience_years,max_experience_years,experience_evidence,persona_tags,
      visa_supported,relocation_assistance,salary_range,salary_currency,link_status,last_link_checked_at
      FROM datapulse.jobs WHERE {where} ORDER BY {sorting}
      LIMIT ${len(params)-1} OFFSET ${len(params)}'''
    async with pool.acquire() as conn:
        total=await conn.fetchval(total_sql,*params[:-2])
        rows=await conn.fetch(sql,*params)
        scanned=await conn.fetchval('SELECT max(completed_at) FROM datapulse.ingestion_runs')
    items=[]
    for r in rows:
        data=dict(r)
        data['fit_breakdown']=match_breakdown(data['domain_category'],data['tech_stack'],
            data['persona_tags'],data['min_experience_years'],data['max_experience_years'])
        items.append(JobResponse.model_validate(data))
    return SearchPage(items=items,total=total,
                      page=page,limit=limit,scanned_at=scanned)


@app.get('/api/v1/profiles')
async def available_profiles():
    return {'profiles':[
        {'id':SAP_EXPERIENCED,'label':'SAP Basis · Experienced','countries':['DE','IN'],
         'experience':'3–6 years (around 5)','min_experience_years':3,'max_experience_years':6},
        {'id':SAP_STUDENT,'label':'SAP Basis · Student / Entry','countries':['DE'],
         'experience':'Not restricted','min_experience_years':None,'max_experience_years':None},
        {'id':DE_JUNIOR,'label':'Data Engineering · Junior / Entry','countries':['DE'],
         'experience':'Up to 2 years','min_experience_years':0,'max_experience_years':2},
    ]}


@app.get('/api/v1/sources')
async def source_stats(request:Request):
    pool=db(request)
    rows=await pool.fetch('''SELECT source_name,source_type::text,COUNT(*) AS total
      FROM datapulse.jobs WHERE is_active AND NOT needs_review GROUP BY source_name,source_type
      ORDER BY total DESC''')
    return {'sources':[dict(x) for x in rows]}


class IngestCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_title:str=Field(min_length=3,max_length=300)
    company_name:str=Field(min_length=2,max_length=250)
    location:str=Field(min_length=2,max_length=260)
    description:str=Field(default='',max_length=20000)
    job_url:str
    source_platform:Literal['Greenhouse','Lever','Ashby','Personio','Workday','SuccessFactors','SmartRecruiters','Company_Portal']
    source_id:str=Field(min_length=2,max_length=300)
    tech_stack:list[str]=Field(default_factory=list,max_length=40)
    date_posted:str|None=None
    date_evidence:str|None=None
    country_code:str|None=None
    remote_eligible_germany:bool=False
    remote_eligible_india:bool=False
    work_model:str|None=None
    job_level:str|None=None
    employment_type:str|None=None
    structured_experience_years:str|None=None


class IngestBatch(BaseModel):
    jobs:list[IngestCandidate]=Field(min_length=1,max_length=25)


def check_ingest_auth(authorization:Annotated[str|None,Header()]=None):
    secret=os.getenv('INGEST_TOKEN','')
    candidate=(authorization or '').removeprefix('Bearer ').strip()
    if len(secret)<32 or not hmac.compare_digest(candidate,secret):
        raise HTTPException(401,'Authenticated ingestion required')


DEFAULT_CRITERIA={
 'filters':{'min_distinct_tech_tags':3,'english_friendly_only':True,
            'min_resume_match_score':.70,'exclude_senior':True,
            'target_levels':['Working Student','Internship','Junior','Mid']}
}


@app.post('/api/v1/internal/ingest',dependencies=[Depends(check_ingest_auth)])
async def ingest(batch:IngestBatch,request:Request):
    pool=db(request)
    result={'inserted':0,'updated':0,'rejected':[],'errors':[]}
    for item in batch.jobs:
        raw=RawJob(source_platform=item.source_platform,source_id=item.source_id,
                   company_name=item.company_name,job_title=item.job_title,
                   location=item.location,job_url=item.job_url,description=item.description,
                   date_posted=item.date_posted,date_evidence=item.date_evidence,
                   country_code=item.country_code,remote_eligible_germany=item.remote_eligible_germany,
                   remote_eligible_india=item.remote_eligible_india,
                   work_model=item.work_model,job_level=item.job_level,
                   employment_type=item.employment_type,structured_experience_years=item.structured_experience_years,
                   tech_stack=item.tech_stack)
        normalized,why=normalize_for_profiles(raw)
        if normalized is None:
            result['rejected'].append({'source_id':item.source_id,'reason':why});continue
        try:
            status,_=await upsert_job(pool,normalized,item.description)
            result[status]+=1
        except Exception:
            # Never include full SQL/connection string in untrusted API responses.
            result['errors'].append({'source_id':item.source_id,'error':'database_write_failed'})
    await pool.execute('''INSERT INTO datapulse.ingestion_runs(completed_at,sources_attempted,sources_succeeded,
       jobs_seen,accepted,inserted,updated,rejected,failures)
       VALUES(now(),1,1,$1,$2,$3,$4,$5::jsonb,$6::jsonb)''',
       len(batch.jobs), result['inserted']+result['updated'], result['inserted'], result['updated'],
       json.dumps(result['rejected']),json.dumps(result['errors']))
    return result


async def verified_user(authorization:Annotated[str|None,Header()]=None)->UUID:
    url=os.getenv('SUPABASE_URL','').rstrip('/')
    key=os.getenv('SUPABASE_PUBLISHABLE_KEY','')
    token=(authorization or '').removeprefix('Bearer ').strip()
    if not url or not key:
        raise HTTPException(503,'User authentication not configured')
    if not token:raise HTTPException(401,'Sign in required')
    async with httpx.AsyncClient(timeout=7.0) as client:
        try:
            resp=await client.get(url+'/auth/v1/user',headers={'apikey':key,'Authorization':f'Bearer {token}'})
        except httpx.HTTPError:raise HTTPException(503,'Authentication provider unavailable')
    if resp.status_code!=200:raise HTTPException(401,'Invalid session')
    try:return UUID(resp.json()['id'])
    except (KeyError,ValueError,TypeError):raise HTTPException(401,'Invalid user identity')


@app.get('/api/v1/bookmarks')
async def bookmarks(request:Request,user_id:UUID=Depends(verified_user)):
    pool=db(request)
    rows=await pool.fetch('''SELECT j.id::text,j.job_title,j.company_name,j.location,
      j.official_apply_url,j.source_name,j.source_type::text,j.match_score,s.saved_at,s.application_status,s.status_updated_at
      FROM datapulse.user_saved_jobs s JOIN datapulse.jobs j ON j.id=s.job_id
      WHERE s.user_id=$1 ORDER BY s.saved_at DESC''',user_id)
    return {'items':[dict(r) for r in rows]}


@app.post('/api/v1/bookmarks/{job_id}')
async def save_bookmark(job_id:UUID,request:Request,user_id:UUID=Depends(verified_user)):
    pool=db(request)
    await pool.execute('''INSERT INTO datapulse.user_saved_jobs(user_id,job_id)
       VALUES($1,$2) ON CONFLICT (user_id,job_id) DO NOTHING''',user_id,job_id)
    return {'saved':True}


class ApplicationStatusUpdate(BaseModel):
    status:Literal['Saved','Applied','Interviewing','Archived']


@app.patch('/api/v1/bookmarks/{job_id}/status')
async def set_application_status(job_id:UUID, payload:ApplicationStatusUpdate,
                                 request:Request,user_id:UUID=Depends(verified_user)):
    pool=db(request)
    changed=await pool.fetchval('UPDATE datapulse.user_saved_jobs SET application_status=$3,status_updated_at=now() WHERE user_id=$1 AND job_id=$2 RETURNING application_status',
      user_id,job_id,payload.status)
    if changed is None:raise HTTPException(404,'Saved job not found')
    return {'job_id':str(job_id),'status':changed}


@app.delete('/api/v1/bookmarks/{job_id}')
async def remove_bookmark(job_id:UUID,request:Request,user_id:UUID=Depends(verified_user)):
    pool=db(request)
    await pool.execute('DELETE FROM datapulse.user_saved_jobs WHERE user_id=$1 AND job_id=$2',user_id,job_id)
    return {'saved':False}

# A restricted read-only ATS scan endpoint. This does not enable job ingestion.
from .scheduled_scan import router as scan_router
app.include_router(scan_router)
