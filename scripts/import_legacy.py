"""One-time audited import from an owner-provided ChatGPT Sites export.

Dry-run by default. No account credential or token is part of this script.
Example:
 python scripts/import_legacy.py --jobs-export legacy-jobs.json
 python scripts/import_legacy.py --jobs-export legacy-jobs.json --saved-export saved.json \
   --user-id ACTUAL_SUPABASE_AUTH_UUID --commit

The new Postgres UUID job IDs cannot equal the original Site's opaque IDs.
`legacy_job_ids` preserves a complete mapping; duplicate legacy postings may map to one new job.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.db import identity, source_type_for

NAMESPACE=uuid.UUID('9c663494-4df1-433b-825c-6d12ebc529aa')


def read_list(path:Path,key:str)->list:
    data=json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data,list):return data
    if isinstance(data,dict) and isinstance(data.get(key),list):return data[key]
    raise ValueError(f'Expected array or object containing {key} array')


def convert(row:dict)->dict|None:
    title=str(row.get('job_title') or row.get('title') or '').strip()
    company=str(row.get('company_name') or row.get('company') or '').strip()
    location=str(row.get('location') or '').strip()
    old_id=str(row.get('id') or row.get('source_key') or '').strip()
    url=str(row.get('official_apply_url') or row.get('job_url') or row.get('url') or '').strip()
    p=urlsplit(url)
    if not all([title,company,location,old_id,p.scheme=='https',p.hostname]):return None
    score=row.get('match_score',row.get('datapulse_fit_score',0))
    try: score=float(str(score).strip('%'));score=score/100 if score>1 else score
    except (TypeError,ValueError):score=0
    score=max(0,min(1,score))
    raw_level=str(row.get('level') or row.get('careerStage') or row.get('job_level') or 'Mid')
    level={'Working Student':'Working Student','Internship':'Internship','Junior':'Junior','Mid':'Mid',
           'Senior':'Senior','Lead':'Lead'}.get(raw_level,'Mid')
    work=str(row.get('work_mode') or row.get('workModel') or 'Unknown')
    work=work if work in ('On-site','Hybrid','Remote') else 'Unknown'
    lang=str(row.get('language_required') or 'Unknown')
    tech=row.get('tech_stack') or row.get('skills') or []
    if not isinstance(tech,list):tech=[]
    source=str(row.get('source_name') or row.get('source') or row.get('source_platform') or 'DataPulse')
    original_date=str(row.get('date_posted') or row.get('publishedAt') or '')[:10]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(original_date)):original_date=None
    return {'old_id':old_id,'title':title,'company':company,'location':location,'url':url,
            'source':source,'source_type':source_type_for(source),'description':str(row.get('description') or '')[:20000],
            'skills':[str(t) for t in tech][:50],'score':score,'level':level,'work':work,
            'english':bool(row.get('englishFriendly') or row.get('is_english_friendly') or lang in ('English','Bilingual')),
            'lang':lang,'date':original_date,
            'needs_review':not bool(row.get('is_active',True)) or score<.7}


async def load(rows:list[dict],saved:list[dict],user_id:uuid.UUID|None,dsn:str)->dict:
    import asyncpg
    conn=await asyncpg.connect(dsn=dsn)
    tally={'inserted':0,'merged':0,'bookmarks_saved':0,'skipped':0}
    try:
        async with conn.transaction():
            for r in rows:
                norm=[identity(r['company']),identity(r['title']),identity(r['location'])]
                existing=await conn.fetchval('''SELECT id FROM datapulse.jobs WHERE
                    normalized_company_name=$1 AND normalized_job_title=$2 AND normalized_location=$3''',*norm)
                jid=existing or uuid.uuid5(NAMESPACE,r['old_id'])
                if existing:tally['merged']+=1
                else:
                    await conn.execute('''INSERT INTO datapulse.jobs
                      (id,job_title,company_name,location,region,normalized_company_name,normalized_job_title,
                       normalized_location,work_mode,level,is_english_friendly,language_required,description,
                       tech_stack,match_score,official_apply_url,source_name,source_type,source_key,date_posted,needs_review)
                      VALUES ($1,$2,$3,$4,NULL,$5,$6,$7,$8::datapulse.work_mode_enum,$9::datapulse.level_enum,
                       $10,$11,$12,$13::text[],$14,$15,$16,$17::datapulse.source_type_enum,$18,$19::date,$20)''',
                       jid,r['title'],r['company'],r['location'],*norm,r['work'],r['level'],
                       r['english'],r['lang'],r['description'],r['skills'],r['score'],r['url'],r['source'],
                       r['source_type'],'legacy:'+r['old_id'],r['date'],r['needs_review'])
                    tally['inserted']+=1
                await conn.execute('''INSERT INTO datapulse.legacy_job_ids(legacy_id,job_id)
                   VALUES($1,$2) ON CONFLICT(legacy_id) DO UPDATE SET job_id=EXCLUDED.job_id''',r['old_id'],jid)
                await conn.execute('''INSERT INTO datapulse.job_sources(job_id,source_key,source_name,source_type,apply_url)
                   VALUES($1,$2,$3,$4::datapulse.source_type_enum,$5) ON CONFLICT DO NOTHING''',
                   jid,'legacy:'+r['old_id'],r['source'],r['source_type'],r['url'])
            if saved and not user_id:
                raise ValueError('To migrate saved jobs, provide the real authenticated Supabase user UUID')
            for item in saved:
                old=str(item.get('job_id') or item.get('jobId') or item.get('id') or '')
                jid=await conn.fetchval('SELECT job_id FROM datapulse.legacy_job_ids WHERE legacy_id=$1',old)
                if not jid:
                    tally['skipped']+=1;continue
                await conn.execute('''INSERT INTO datapulse.user_saved_jobs(user_id,job_id) VALUES($1,$2)
                   ON CONFLICT(user_id,job_id) DO NOTHING''',user_id,jid)
                tally['bookmarks_saved']+=1
    finally:await conn.close()
    return tally


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--jobs-export',type=Path,required=True)
    ap.add_argument('--saved-export',type=Path)
    ap.add_argument('--user-id',type=uuid.UUID)
    ap.add_argument('--commit',action='store_true')
    opts=ap.parse_args()
    input_rows=read_list(opts.jobs_export,'jobs')
    processed=[x for x in (convert(j) for j in input_rows) if x]
    saved=read_list(opts.saved_export,'saved_jobs') if opts.saved_export else []
    if not opts.commit:
        print(json.dumps({'dry_run':True,'source_count':len(input_rows),'valid_candidates':len(processed),
                          'skipped_invalid':len(input_rows)-len(processed),'saved_items':len(saved),
                          'would_merge_duplicates':len(processed)-len({(identity(j['company']),identity(j['title']),identity(j['location'])) for j in processed})},indent=2))
    else:
        dsn=os.getenv('DATABASE_URL')
        if not dsn:raise SystemExit('DATABASE_URL is required; do not paste into chat')
        print(json.dumps(asyncio.run(load(processed,saved,opts.user_id,dsn)),indent=2))

if __name__=='__main__':main()
