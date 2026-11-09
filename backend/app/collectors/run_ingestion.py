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
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from datapulse_ingestion.connectors import CONNECTORS, Fetcher
from datapulse_ingestion.models import RawJob, Job
from datapulse_ingestion.profiles import (normalize_for_profiles, PERSONA_IDS,
    SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR)
from app.link_checker import inspect_link, safe_ats_url

ROOT=Path(__file__).resolve().parents[3]
CRITERIA={"filters":{"min_distinct_tech_tags":0,"english_friendly_only":False,
    "min_resume_match_score":0,"exclude_senior":True,
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


def eligible_boards(path:Path,source:str|None=None,board_filter:str|None=None,
                    approved_only:bool=False)->list[dict]:
    data=json.loads(path.read_text(encoding='utf-8'))
    boards=data['boards'] if isinstance(data,dict) else data
    return [x for x in boards if x.get('enabled')
            and (not approved_only or x.get('auto_import_approved') is True)
            and (not source or x.get('type','').lower()==source.lower())
            and (not board_filter or board_filter.lower() in (x.get('board','')+' '+x.get('company','')).lower())]


SOURCE_PLATFORMS={
    'workday':'Workday','personio':'Personio','smartrecruiters':'SmartRecruiters',
    'greenhouse':'Greenhouse','lever':'Lever','ashby':'Ashby',
}


def approved_source_url(raw:RawJob,board:dict)->bool:
    """A valid ATS hostname alone does not prove the link belongs to this employer."""
    kind=board.get('type','').lower()
    if (board.get('auto_import_approved') is not True or not board.get('enabled')
            or raw.company_name!=board.get('company')
            or raw.source_platform!=SOURCE_PLATFORMS.get(kind)
            or not safe_ats_url(raw.job_url)):
        return False
    p=urlsplit(raw.job_url)
    host=(p.hostname or '').lower()
    path=p.path.lower()
    token=str(board.get('board','')).lower()
    if kind=='workday':
        career=urlsplit(board.get('career_url',''))
        parts=[x for x in career.path.split('/') if x]
        site=parts[-1].lower() if parts else ''
        return host==(career.hostname or '').lower() and bool(site) and path.startswith('/'+site+'/job/')
    if kind=='personio':
        return host==f'{token}.jobs.personio.de' and path.startswith('/job/')
    if kind=='smartrecruiters':
        return host=='jobs.smartrecruiters.com' and path.startswith('/'+token+'/')
    if kind=='greenhouse':
        return host in ('boards.greenhouse.io','job-boards.greenhouse.io') and ('/'+token+'/') in path
    if kind=='lever':
        return host=='jobs.lever.co' and path.startswith('/'+token+'/')
    if kind=='ashby':
        return host=='jobs.ashbyhq.com' and path.startswith('/'+token+'/')
    return False


def to_payload(raw:RawJob)->dict[str,Any]:
    return {'job_title':raw.job_title,'company_name':raw.company_name,'location':raw.location,
     'description':raw.description[:20000],'job_url':raw.job_url,
     'source_platform':raw.source_platform,'source_id':raw.source_id,
     'tech_stack':raw.tech_stack,'date_posted':raw.date_posted,
     'date_evidence':raw.date_evidence,'country_code':raw.country_code,
     'remote_eligible_germany':raw.remote_eligible_germany,
     'remote_eligible_india':raw.remote_eligible_india,
     'work_model':raw.work_model,'job_level':raw.job_level,
     'structured_experience_years':raw.structured_experience_years,
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
                  target_profiles:str|None=None,board_offset:int=0,
                  approved_only:bool=False,verify_links:bool=False,
                  persist:Callable[[list[tuple[RawJob,Job]]],Awaitable[dict]]|None=None)->dict:
    if not dry_run and not approved_only:
        raise ValueError('Production writes require the explicit approved-source allowlist')
    if not dry_run and fixture_file:
        raise ValueError('Production writes cannot use synthetic fixture data')
    if dry_run and persist is not None:
        raise ValueError('Read-only scans cannot receive a database writer')
    verify_links=verify_links or not dry_run
    selected=resolve_profiles(target_profiles)
    boards=eligible_boards(source_file,source,board_filter,approved_only=approved_only)
    # Legacy 11 sources remain the default for the scheduled scanner until
    # the expanded registry has been benchmarked and reviewed.
    if not expand_registry and not approved_only and not source and not board_filter:
        boards=boards[:11]
    if boards and board_offset:
        board_offset=board_offset%len(boards)
        boards=boards[board_offset:]+boards[:board_offset]
    boards=boards[:max_boards]
    if approved_only and not boards:
        raise ValueError('No enabled, explicitly approved ATS sources are configured')
    summary={'dry_run':dry_run,'expanded_registry':expand_registry,'approved_only':approved_only,
             'target_profiles':list(selected),'per_profile':{p:{'accepted':0,'samples':[]} for p in selected},
             'board_results':[],'boards_attempted':len(boards),'boards_succeeded':0,
             'jobs_seen':0,'accepted':0,'rejected':{},'errors':{},'inserted':0,'updated':0,
             'server_rejected':0,'links_verified':0,'sample_jobs':[]}
    if fixture_file and len(boards)!=1:raise ValueError('Fixture mode needs exactly one selected board')
    accepted=[]
    accepted_jobs:list[tuple[RawJob,Job]]=[]
    seen_sources=set()
    link_client=None
    for board in boards:
        label=f"{board['type']}:{board['company']}"
        board_result={'board':label,'seen':0,'accepted':0,'links_verified':0,'status':'pending'}
        try:
            connector=CONNECTORS[board['type']]
            if fixture_file:
                # Only for tests/demos: fake fetch returns the fixture and cannot access the web.
                class FixtureFetcher:
                    def get(self,*a,**kw):return json.loads(fixture_file.read_text())
                fetched=connector(board,FixtureFetcher())
            else:
                def fetch_jobs():
                    fetcher=Fetcher()
                    try:
                        return connector(board,fetcher)
                    finally:
                        fetcher.client.close()
                fetched=await asyncio.to_thread(fetch_jobs)
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
                if approved_only and not approved_source_url(raw,board):
                    summary['rejected']['source_url_not_approved']=summary['rejected'].get('source_url_not_approved',0)+1
                    continue
                if approved_only and raw.source_key in seen_sources:
                    summary['rejected']['duplicate_source_job']=summary['rejected'].get('duplicate_source_job',0)+1
                    continue
                if approved_only and (board_result['accepted']>=min(10,max(1,int(board.get('max_auto_import',5))))
                                      or len(accepted)>=25):
                    summary['rejected']['daily_import_limit']=summary['rejected'].get('daily_import_limit',0)+1
                    continue
                if verify_links:
                    if link_client is None:
                        link_client=httpx.AsyncClient(headers={'User-Agent':'DataPulse/2.1 verified-import'},
                                                       follow_redirects=False)
                    verdict=await inspect_link(raw.job_url,link_client)
                    if verdict.status!='live':
                        reason='link_'+verdict.status
                        summary['rejected'][reason]=summary['rejected'].get(reason,0)+1
                        continue
                    summary['links_verified']+=1
                    board_result['links_verified']+=1
                if approved_only:
                    seen_sources.add(raw.source_key)
                accepted.append(to_payload(raw))
                accepted_jobs.append((raw,job))
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
    if link_client is not None:
        await link_client.aclose()
    summary['accepted']=len(accepted)
    if not dry_run and summary['accepted']:
        if persist is not None:
            # Only the authenticated Render scheduler supplies this private,
            # server-side writer. The standalone worker uses the API token.
            result=await persist(accepted_jobs)
        else:
            if target!='api':raise ValueError('Production writes require authenticated API target')
            if not api_base_url:raise ValueError('Set API_BASE_URL')
            result=await push_batches(accepted,api_base_url,os.getenv('INGEST_TOKEN',''))
        summary.update(result)
    return summary


def main():
    parser=argparse.ArgumentParser(description='DataPulse nationwide public ATS collection')
    parser.add_argument('--source-file',type=Path,default=ROOT/'config'/'verified-boards.json')
    parser.add_argument('--source',choices=sorted(CONNECTORS)+['partner_directories'],default=None)
    parser.add_argument('--sources',default=None,
        help='Read-only sources: sap_partners,databricks_partners,softgarden,successfactors')
    parser.add_argument('--output-report',type=Path,default=None,
        help='Write JSON run summary to a local path; never includes credentials')
    parser.add_argument('--board',default=None)
    parser.add_argument('--max-boards',type=int,default=60)
    parser.add_argument('--expand-registry',action='store_true',help='Include newly verified public ATS feeds')
    parser.add_argument('--ecosystems',default='sap,databricks',help='Only for partner_directories')
    parser.add_argument('--profiles',default=None,help='Alias for --target-profiles in partner mode')
    parser.add_argument('--max-partners',type=int,default=16)
    parser.add_argument('--max-feeds',type=int,default=16)
    parser.add_argument('--target-profiles',default=None,
        help='Comma-separated aliases: sap_basis_5y,sap_basis_student,data_eng_junior')
    parser.add_argument('--dry-run',type=bool_arg,nargs='?',const=True,default=True,
        help='True by default; --dry-run=false requires --apply')
    parser.add_argument('--apply',action='store_true',
        help='Explicit production write opt-in; requires authenticated API and --dry-run=false')
    parser.add_argument('--approved-only',action='store_true',
        help='Restrict to enabled boards with auto_import_approved=true; required for writes')
    parser.add_argument('--verify-links',action='store_true',
        help='Check official ATS application URLs in dry runs; always on for writes')
    parser.add_argument('--target',default='api',choices=['api'])
    parser.add_argument('--api-base-url',default=os.getenv('API_BASE_URL'))
    parser.add_argument('--fixture',type=Path,default=None,help='Local synthetic test fixture (never live data)')
    args=parser.parse_args()
    if args.sources or args.source=='partner_directories':
        if not args.dry_run or args.apply:
            parser.error('Partner verification is strictly read-only; --apply disabled')
        from .bounded_partner_scan import scan_bounded, SOURCES
        if args.sources:
            sources={x.strip().lower() for x in args.sources.split(',') if x.strip()}
            if not sources or not sources <= set(SOURCES):
                parser.error('Use --sources sap_partners,databricks_partners,softgarden,successfactors')
        else:
            ecosystems={x.strip().lower() for x in args.ecosystems.split(',') if x.strip()}
            if not ecosystems or not ecosystems <= {'sap','databricks'}:
                parser.error('Use --ecosystems sap,databricks or a subset')
            sources=({'sap_partners'} if 'sap' in ecosystems else set()) | (
                     {'databricks_partners'} if 'databricks' in ecosystems else set())
        chosen=resolve_profiles(args.profiles or args.target_profiles)
        targets=json.loads((ROOT/'config/partner_directory_targets.json').read_text())
        verified=json.loads((ROOT/'config/verified-boards.json').read_text())
        report=asyncio.run(scan_bounded(
            targets['targets'],verified['boards'],sources=sources,profiles=chosen,
            max_partners=max(0,min(args.max_partners,32)),
            max_feeds=max(0,min(args.max_feeds,16))))
        rendered=json.dumps(report,ensure_ascii=False,indent=2)
        if args.output_report:
            args.output_report.parent.mkdir(parents=True,exist_ok=True)
            args.output_report.write_text(rendered+'\n',encoding='utf-8')
        print(rendered)
        return
    if not args.dry_run and not args.apply:
        parser.error('Refusing production writes without --apply')
    if args.apply and args.dry_run:
        parser.error('To apply writes, also specify --dry-run=false')
    if not args.dry_run and not args.approved_only:
        parser.error('Production writes require --approved-only')
    report=asyncio.run(collect(args.source_file,source=args.source,board_filter=args.board,
         dry_run=args.dry_run,target=args.target,api_base_url=args.api_base_url,
         max_boards=args.max_boards,fixture_file=args.fixture,
         expand_registry=args.expand_registry,target_profiles=args.target_profiles,
         approved_only=args.approved_only,verify_links=args.verify_links))
    if args.output_report:
        args.output_report.parent.mkdir(parents=True,exist_ok=True)
        args.output_report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['errors'] or report['boards_succeeded']==0 or (not args.dry_run and report['accepted'] and report['inserted']+report['updated']<report['accepted']):
        raise SystemExit(2)

if __name__=='__main__':main()
