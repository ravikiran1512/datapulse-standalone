"""Conservative, bounded stale-listing maintenance.

Never trust a HEAD 404 by itself; require a GET confirmation and two daily
checks before disabling. Do not follow redirects to arbitrary hosts or internal
network targets. All ambiguous responses remain active and flagged for review.
"""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin
import ipaddress
import re
import httpx

ATS_SUFFIXES = (
    '.jobs.personio.de','.jobs.personio.com','.myworkdayjobs.com',
    '.greenhouse.io','.lever.co','.smartrecruiters.com','.ashbyhq.com',
)
EXACT_HOSTS = {'jobs.smartrecruiters.com','boards.greenhouse.io','job-boards.greenhouse.io',
               'jobs.lever.co','jobs.ashbyhq.com'}
CLOSED_TEXT = re.compile(r'\b(?:this\s+position\s+(?:has\s+been\s+filled|is\s+closed|is\s+no\s+longer\s+available)|job\s+(?:is\s+)?no\s+longer\s+(?:available|open)|posting\s+has\s+expired|vacancy\s+is\s+closed|diese\s+stelle\s+ist\s+nicht\s+mehr\s+verf[uü]gbar)\b', re.I)


def safe_ats_url(url:str)->bool:
    try:
        p=urlsplit(url);host=(p.hostname or '').lower().rstrip('.')
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not host:return False
        try:ipaddress.ip_address(host);return False
        except ValueError:pass
        return host in EXACT_HOSTS or any(host.endswith(s) for s in ATS_SUFFIXES)
    except ValueError:
        return False


@dataclass(frozen=True)
class LinkResult:
    status:str    # live / confirmed_closed / uncertain / unchecked
    reason:str


async def inspect_link(url:str,client:httpx.AsyncClient)->LinkResult:
    if not safe_ats_url(url):return LinkResult('unchecked','Unsupported employer host; manual review needed')
    current=url
    for _ in range(4):
        try:
            h=await client.head(current,follow_redirects=False,timeout=8)
            if h.status_code in (301,302,303,307,308):
                nxt=urljoin(current,h.headers.get('location',''))
                if nxt==current or not safe_ats_url(nxt):return LinkResult('uncertain','Redirect outside verified ATS or loop')
                current=nxt;continue
            # HEAD is frequently unsupported or misleading on ATS portals.
            g=await client.get(current,follow_redirects=False,timeout=12)
            if g.status_code in (301,302,303,307,308):
                nxt=urljoin(current,g.headers.get('location',''))
                if nxt==current or not safe_ats_url(nxt):return LinkResult('uncertain','Redirect outside verified ATS or loop')
                current=nxt;continue
            if g.status_code in (404,410):return LinkResult('confirmed_closed',f'GET {g.status_code}')
            if g.status_code==200:
                if 'text/html' in g.headers.get('content-type','').lower() and CLOSED_TEXT.search(g.text[:60000]):
                    return LinkResult('confirmed_closed','Employer closed-position notice')
                return LinkResult('live','Employer URL reachable')
            return LinkResult('uncertain',f'GET {g.status_code}')
        except (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError):
            return LinkResult('uncertain','Network or timeout, not proof of expiry')
    return LinkResult('uncertain','Too many redirects')


async def check_stale_jobs(pool,limit:int=25)->dict:
    """One bounded batch/day. Disable only after two confirmed closed checks.

    Private ATS URLs are never requested. No job or bookmark is ever deleted.
    """
    limit=max(1,min(limit,50))
    rows=await pool.fetch('''SELECT id,official_apply_url,link_failures FROM datapulse.jobs
        WHERE is_active AND NOT needs_review
        ORDER BY last_link_checked_at ASC NULLS FIRST LIMIT $1''',limit)
    counts={'examined':len(rows),'live':0,'closed':0,'uncertain':0,'unchecked':0,'deactivated':0}
    async with httpx.AsyncClient(headers={'User-Agent':'DataPulse/2.1 link-checker (daily, bounded)'},
                                follow_redirects=False) as client:
        for row in rows:
            result=await inspect_link(row['official_apply_url'],client)
            old=row['link_failures'] or 0
            failures=(old+1 if result.status=='confirmed_closed' else 0)
            deactivate=result.status=='confirmed_closed' and failures>=2
            counts[result.status]+=1
            if deactivate:counts['deactivated']+=1
            await pool.execute('''UPDATE datapulse.jobs
                SET last_link_checked_at=now(),link_status=$2,link_failures=$3,
                    link_detail=$4,is_active=CASE WHEN $5 THEN false ELSE is_active END,
                    link_last_confirmed_at=CASE WHEN $2='live' THEN now() ELSE link_last_confirmed_at END,
                    updated_at=CASE WHEN $5 THEN now() ELSE updated_at END
                WHERE id=$1''',row['id'],result.status,failures,result.reason,deactivate)
    return counts
