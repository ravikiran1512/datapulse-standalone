"""Bounded, read-only partner career resolver; never invent company ATS slugs.

Only follows links actually present on a configured official employer career page.
Discovery emits candidate feed URLs for human promotion to verified-boards.json;
it never writes production jobs or assumes a partner certification tier.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[3]
HOST_SUFFIXES=('.jobs.personio.de','.jobs.personio.com','.greenhouse.io',
 '.lever.co','.ashbyhq.com','.myworkdayjobs.com','.smartrecruiters.com',
 '.softgarden.io','.softgarden.de','.successfactors.com')


def classify_ats(url:str)->str|None:
    host=(urlsplit(url).hostname or '').lower()
    if host.endswith(('.jobs.personio.de','.jobs.personio.com')):return 'personio'
    if host.endswith('.greenhouse.io'):return 'greenhouse'
    if host.endswith('.lever.co'):return 'lever'
    if host.endswith('.ashbyhq.com'):return 'ashby'
    if host.endswith('.myworkdayjobs.com'):return 'workday'
    if host.endswith('.smartrecruiters.com'):return 'smartrecruiters'
    if host.endswith(('.softgarden.io','.softgarden.de')):return 'softgarden'
    if host.endswith('.successfactors.com'):return 'successfactors'
    return None


def linked_sources(html:str,base:str)->list[dict]:
    result=[];seen=set()
    for a in BeautifulSoup(html,'html.parser').select('a[href]'):
        url=urljoin(base,a.get('href',''))
        if urlsplit(url).scheme!='https' or url in seen:continue
        typ=classify_ats(url)
        if typ:
            result.append({'ats':typ,'url':url,'evidence':'Linked from official career page'})
            seen.add(url)
    return result[:25]


async def check_robots(client:httpx.AsyncClient,url:str)->bool:
    parsed=urlsplit(url);root=f'{parsed.scheme}://{parsed.netloc}'
    try:
        r=await client.get(root+'/robots.txt',timeout=7,follow_redirects=False)
        if r.status_code!=200:return False  # conservative: unverified access
        rules=RobotFileParser();rules.parse(r.text.splitlines())
        return rules.can_fetch('DataPulseBot/1.0',url)
    except httpx.HTTPError:
        return False


async def discover(targets:dict,max_targets:int=10)->list[dict]:
    results=[]
    async with httpx.AsyncClient(headers={'User-Agent':'DataPulseBot/1.0 (public partner career link resolver)'},
                                 follow_redirects=False,timeout=12) as client:
        for target in targets['sources'][:max_targets]:
            row={**target,'status':'not_verified','candidates':[]}
            url=target['career_page']
            if not url.startswith('https://') or not await check_robots(client,url):
                row['reason']='Robots access not verified';results.append(row);continue
            try:
                response=await client.get(url)
                if response.status_code!=200:
                    row['reason']=f'HTTP {response.status_code}';results.append(row);continue
                if len(response.content)>1500000:
                    row['reason']='Page too large';results.append(row);continue
                candidates=linked_sources(response.text,url)
                if classify_ats(url):candidates.insert(0,{'ats':classify_ats(url),'url':url,'evidence':'Official careers page itself'})
                row.update(status='needs_feed_verification',candidates=candidates,reason='Links require ATS feed schema and robots verification')
            except httpx.HTTPError:
                row['reason']='HTTP fetch failed'
            results.append(row)
            await asyncio.sleep(.4)
    return results


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--targets',type=Path,default=ROOT/'config/partner_targets.json')
    p.add_argument('--max-targets',type=int,default=8)
    p.add_argument('--output',type=Path,default=None)
    args=p.parse_args()
    data=json.loads(args.targets.read_text())
    rows=asyncio.run(discover(data,max_targets=max(0,min(args.max_targets,15))))
    result={'read_only':True,'verified_employer_feeds':0,'candidate_sources':rows,
            'unresolved_companies':data.get('unresolved_companies',{})}
    formatted=json.dumps(result,indent=2,ensure_ascii=False)
    if args.output:args.output.write_text(formatted+'\n')
    print(formatted)

if __name__=='__main__':main()
