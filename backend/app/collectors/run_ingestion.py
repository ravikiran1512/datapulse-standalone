"""Daily modular public-ATS crawler. Writes through authenticated DataPulse API.

Example:
  python -m app.collectors.run_ingestion --source greenhouse --dry-run
  python -m app.collectors.run_ingestion --target api --api-base-url https://api.example.com

Does NOT evade robots, authentication, CAPTCHA or rate limits.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from datapulse_ingestion.connectors import CONNECTORS, Fetcher
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import normalize_for_profiles

ROOT=Path(__file__).resolve().parents[3]
CRITERIA={"filters":{"min_distinct_tech_tags":3,"english_friendly_only":True,
    "min_resume_match_score":.70,"exclude_senior":True,
    "target_levels":["Working Student","Internship","Junior","Mid"]}}


def eligible_boards(path:Path,source:str|None=None,board_filter:str|None=None)->list[dict]:
    data=json.loads(path.read_text(encoding='utf-8'))
    boards=data['boards'] if isinstance(data,dict) else data
    return [x for x in boards if x.get('enabled')
            and (not source or x.get('type','').lower()==source.lower())
            and (not board_filter or board_filter.lower() in (x.get('board','')+' '+x.get('company','')).lower())]


def to_payload(raw:RawJob)->dict[str,Any]:
    return {'job_title':raw.job_title,'company_name':raw.company_name,'location':raw.location,
     'description':raw.description[:20000],'job_url':raw.job_url,
     'source_platform':raw.source_platform,'source_id':raw.source_id,
     'tech_stack':raw.tech_stack,'date_posted':raw.date_posted,
     'date_evidence':raw.date_evidence,'country_code':raw.country_code,
     'remote_eligible_germany':raw.remote_eligible_germany,
     'remote_eligible_india':raw.remote_eligible_india,
     'work_model':raw.work_model,'job_level':raw.job_level,
     'employment_type':raw.employment_type}


async def push_batches(records:list[dict],api_base_url:str,token:str)->dict:
    if not token or len(token)<32:raise ValueError('Set a 32+ character INGEST_TOKEN in the worker')
    if not api_base_url.startswith('https://'):raise ValueError('API must be HTTPS')
    result=Counter()
    async with httpx.AsyncClient(timeout=100.0,follow_redirects=False) as client:
        for i in range(0,len(records),25):
            chunk=records[i:i+25]
            response=await client.post(api_base_url.rstrip('/')+'/api/v1/internal/ingest',
                headers={'Authorization':f'Bearer {token}'},json={'jobs':chunk})
            response.raise_for_status()
            data=response.json()
            if data.get('errors'):
                raise RuntimeError(f'Batch {i//25+1} had {len(data["errors"])} failed DB writes')
            result.update({'inserted':data.get('inserted',0),'updated':data.get('updated',0),
                           'server_rejected':len(data.get('rejected',[]))})
    return dict(result)


async def collect(source_file:Path,*,source:str|None=None,board_filter:str|None=None,
                  dry_run:bool=True,target:str='api',api_base_url:str|None=None,max_boards:int=60,
                  fixture_file:Path|None=None)->dict:
    boards=eligible_boards(source_file,source,board_filter)[:max_boards]
    summary={'dry_run':dry_run,'boards_attempted':len(boards),'boards_succeeded':0,
             'jobs_seen':0,'accepted':0,'rejected':{},'errors':{},'inserted':0,'updated':0,
             'server_rejected':0,'sample_jobs':[]}
    if fixture_file and len(boards)!=1:raise ValueError('Fixture mode needs exactly one selected board')
    accepted=[]
    for board in boards:
        label=f"{board['type']}:{board['company']}"
        try:
            connector=CONNECTORS[board['type']]
            if fixture_file:
                # Only for tests/demos: fake fetch returns the fixture and cannot access the web.
                class FixtureFetcher:
                    def get(self,*a,**kw):return json.loads(fixture_file.read_text())
                fetched=connector(board,FixtureFetcher())
            else:
                fetched=await asyncio.to_thread(connector,board,Fetcher())
            summary['boards_succeeded']+=1
            summary['jobs_seen']+=len(fetched)
            for raw in fetched:
                job,reason=normalize_for_profiles(raw)
                if job is None:
                    summary['rejected'][reason]=summary['rejected'].get(reason,0)+1
                    continue
                accepted.append(to_payload(raw))
                if len(summary['sample_jobs'])<6:
                    summary['sample_jobs'].append({'title':job.job_title,'company':job.company_name,
                                                   'source':job.source_platform,'match':round(job.technical_match_score or 0,3),'personas':job.persona_tags})
        except Exception as exc:
            summary['errors'][label]=f'{type(exc).__name__}: {str(exc)[:160]}'
    summary['accepted']=len(accepted)
    if not dry_run and summary['accepted']:
        if target!='api':raise ValueError('Production writes require authenticated API target')
        if not api_base_url:raise ValueError('Set API_BASE_URL')
        result=await push_batches(accepted,api_base_url,os.getenv('INGEST_TOKEN',''))
        summary.update(result)
    return summary


def main():
    parser=argparse.ArgumentParser(description='DataPulse nationwide public ATS collection')
    parser.add_argument('--source-file',type=Path,default=ROOT/'config'/'verified-boards.json')
    parser.add_argument('--source',choices=sorted(CONNECTORS),default=None)
    parser.add_argument('--board',default=None)
    parser.add_argument('--max-boards',type=int,default=60)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--target',default='api',choices=['api'])
    parser.add_argument('--api-base-url',default=os.getenv('API_BASE_URL'))
    parser.add_argument('--fixture',type=Path,default=None,help='Local synthetic test fixture (never live data)')
    args=parser.parse_args()
    report=asyncio.run(collect(args.source_file,source=args.source,board_filter=args.board,
         dry_run=args.dry_run,target=args.target,api_base_url=args.api_base_url,
         max_boards=args.max_boards,fixture_file=args.fixture))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['errors'] or report['boards_succeeded']==0 or (not args.dry_run and report['accepted'] and report['inserted']+report['updated']<report['accepted']):
        raise SystemExit(2)

if __name__=='__main__':main()
