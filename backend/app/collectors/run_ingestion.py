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
from datapulse_ingestion.profiles import (normalize_for_profiles, PERSONA_IDS,
    SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR)

ROOT=Path(__file__).resolve().parents[3]
CRITERIA={"filters":{"min_distinct_tech_tags":3,"english_friendly_only":True,
    "min_resume_match_score":.70,"exclude_senior":True,
    "target_levels":["Working Student","Internship","Junior","Mid"]}}


PROFILE_ALIASES = {
    'sap_basis_5y': SAP_EXPERIENCED,
    'sap_basis_experienced': SAP_EXPERIENCED,
    'sap_basis_student': SAP_STUDENT,
    'data_eng_junior': DE_JUNIOR,
    'data_engineering_junior': DE_JUNIOR,
}


def resolve_profiles(selected:str|None)->tuple[str,...]:
    if not selected:
        return PERSONA_IDS
    keys=[x.strip().lower() for x in selected.split(',') if x.strip()]
    if not keys or any(x not in PROFILE_ALIASES for x in keys):
        raise ValueError('Unknown target profile. Use: '+', '.join(sorted(PROFILE_ALIASES)))
    return tuple(dict.fromkeys(PROFILE_ALIASES[x] for x in keys))


def bool_arg(value:str)->bool:
    if value.lower() in ('true','yes','1'):return True
    if value.lower() in ('false','no','0'):return False
    raise argparse.ArgumentTypeError('Expected true or false')


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
                  fixture_file:Path|None=None,expand_registry:bool=False,
                  target_profiles:str|None=None)->dict:
    selected=resolve_profiles(target_profiles)
    boards=eligible_boards(source_file,source,board_filter)
    # Legacy 11 sources remain the default for the scheduled scanner until
    # the expanded registry has been benchmarked and reviewed.
    if not expand_registry and not source and not board_filter:
        boards=boards[:11]
    boards=boards[:max_boards]
    summary={'dry_run':dry_run,'expanded_registry':expand_registry,
             'target_profiles':list(selected),'per_profile':{p:{'accepted':0,'samples':[]} for p in selected},
             'board_results':[],'boards_attempted':len(boards),'boards_succeeded':0,
             'jobs_seen':0,'accepted':0,'rejected':{},'errors':{},'inserted':0,'updated':0,
             'server_rejected':0,'sample_jobs':[]}
    if fixture_file and len(boards)!=1:raise ValueError('Fixture mode needs exactly one selected board')
    accepted=[]
    for board in boards:
        label=f"{board['type']}:{board['company']}"
        board_result={'board':label,'seen':0,'accepted':0,'status':'pending'}
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
            board_result.update({'seen':len(fetched),'status':'ok'})
            for raw in fetched:
                job,reason=normalize_for_profiles(raw)
                if job is None:
                    summary['rejected'][reason]=summary['rejected'].get(reason,0)+1
                    continue
                matched=[p for p in job.persona_tags if p in selected]
                if not matched:
                    summary['rejected']['outside_selected_profiles']=summary['rejected'].get('outside_selected_profiles',0)+1
                    continue
                accepted.append(to_payload(raw))
                board_result['accepted']+=1
                for persona in matched:
                    stats=summary['per_profile'][persona]
                    stats['accepted']+=1
                    if len(stats['samples'])<6:
                        stats['samples'].append({'title':job.job_title,'company':job.company_name,
                                                'country':job.country_code,'experience':job.min_experience_years,
                                                'official_url':job.job_url})
                if len(summary['sample_jobs'])<6:
                    summary['sample_jobs'].append({'title':job.job_title,'company':job.company_name,
                                                   'source':job.source_platform,'match':round(job.technical_match_score or 0,3),'personas':job.persona_tags})
        except Exception as exc:
            error=f'{type(exc).__name__}: {str(exc)[:160]}'
            summary['errors'][label]=error
            board_result.update({'status':'failed','error':error})
        finally:
            summary['board_results'].append(board_result)
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
    parser.add_argument('--expand-registry',action='store_true',help='Include newly verified public ATS feeds')
    parser.add_argument('--target-profiles',default=None,
        help='Comma-separated aliases: sap_basis_5y,sap_basis_student,data_eng_junior')
    parser.add_argument('--dry-run',type=bool_arg,nargs='?',const=True,default=True,
        help='True by default; --dry-run=false requires --apply')
    parser.add_argument('--apply',action='store_true',
        help='Explicit production write opt-in; requires authenticated API and --dry-run=false')
    parser.add_argument('--target',default='api',choices=['api'])
    parser.add_argument('--api-base-url',default=os.getenv('API_BASE_URL'))
    parser.add_argument('--fixture',type=Path,default=None,help='Local synthetic test fixture (never live data)')
    args=parser.parse_args()
    if not args.dry_run and not args.apply:
        parser.error('Refusing production writes without --apply')
    if args.apply and args.dry_run:
        parser.error('To apply writes, also specify --dry-run=false')
    report=asyncio.run(collect(args.source_file,source=args.source,board_filter=args.board,
         dry_run=args.dry_run,target=args.target,api_base_url=args.api_base_url,
         max_boards=args.max_boards,fixture_file=args.fixture,
         expand_registry=args.expand_registry,target_profiles=args.target_profiles))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['errors'] or report['boards_succeeded']==0 or (not args.dry_run and report['accepted'] and report['inserted']+report['updated']<report['accepted']):
        raise SystemExit(2)

if __name__=='__main__':main()
