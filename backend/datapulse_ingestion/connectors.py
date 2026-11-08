"""Public, read-only board connectors. Only scrape employer URLs you have verified and configured.

Never bypass robots, authentication, CAPTCHAs, rate limits or an employer's terms.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin, urlsplit, quote
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from defusedxml import ElementTree as SafeET

from .models import RawJob
from .policy import ROLE, clean, valid_date
from .profiles import SAP_BASIS_ROLE


class SourceError(RuntimeError): pass


def plaintext(html: str | None) -> str:
    return clean(BeautifulSoup(unescape(str(html or "")),"html.parser").get_text(" ",strip=True))


def numeric_timestamp_date(val) -> str | None:
    try:
        if val is None:return None
        num=float(val)
        if num>1e11:num/=1000  # Lever timestamps are usually milliseconds
        return datetime.fromtimestamp(num,timezone.utc).date().isoformat()
    except (ValueError,OverflowError,TypeError):return None


def iso_date(value) -> str | None:
    if not value:return None
    s=str(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}",s):return valid_date(s)
    try:return parsedate_to_datetime(s).date().isoformat()
    except (ValueError,TypeError,IndexError):return None


class Fetcher:
    def __init__(self,*,timeout=15.0,delay_seconds=1.2,client=None,user_agent="DataPulseJobs/0.1 (+contact: configure-email)"):
        self.client=client or httpx.Client(timeout=timeout,headers={"User-Agent":user_agent,"Accept":"application/json, text/xml, text/html"},follow_redirects=False)
        self.delay_seconds=delay_seconds
        self._last:dict[str,float]={}
        self.agent=user_agent.split("/")[0]

    def get(self,url:str,*,params=None,expected_host=None,expect="json",check_robots=False):
        return self._request("GET",url,params=params,expected_host=expected_host,expect=expect,check_robots=check_robots)

    def post(self,url:str,*,json,expected_host=None):
        return self._request("POST",url,json=json,expected_host=expected_host,expect="json")

    def _request(self,method,url,*,expected_host=None,expect="json",check_robots=False,**kw):
        p=urlsplit(url)
        if p.scheme!="https" or not p.hostname or p.username or p.password or p.port not in (None,443):
            raise SourceError("Refusing non-HTTPS or non-public-looking URL")
        if expected_host and p.hostname.lower()!=expected_host.lower():
            raise SourceError(f"Unexpected host: {p.hostname}")
        if p.hostname.lower() in ("localhost","127.0.0.1","0.0.0.0") or p.hostname.endswith((".local",".internal")):
            raise SourceError("Refusing local/private hostname")
        if check_robots and not self.robots_allowed(url):
            raise SourceError("robots.txt disallows scraping this career page")
        host=p.hostname.lower()
        elapsed=time.monotonic()-self._last.get(host,0)
        if elapsed<self.delay_seconds:time.sleep(self.delay_seconds-elapsed)
        self._last[host]=time.monotonic()
        try:
            r=self.client.request(method,url,**kw)
            if r.status_code in (301,302,303,307,308):
                raise SourceError("Redirect not auto-followed; verify destination and configure it explicitly")
            if r.status_code in (401,403,429):
                raise SourceError(f"Employer feed refused access ({r.status_code}); no bypass/retry")
            r.raise_for_status()
            if len(r.content)>10_000_000:raise SourceError("Feed exceeds 10 MB safety limit")
            if expect=="json":return r.json()
            return r.text
        except (httpx.HTTPError,ValueError) as e: raise SourceError(f"Failed request {host}: {type(e).__name__}") from e

    def robots_allowed(self,url:str)->bool:
        p=urlsplit(url)
        if not p.hostname:return False
        robots_url=f"{p.scheme}://{p.netloc}/robots.txt"
        try:
            r=self.client.get(robots_url,headers={"Accept":"text/plain"},follow_redirects=False)
            if r.status_code!=200 or "user-agent:" not in r.text.lower():
                return False  # fail closed for HTML app shells or inaccessible rules
            rp=RobotFileParser();rp.parse(r.text.splitlines());return rp.can_fetch(self.agent,url)
        except httpx.HTTPError:return False


def build_job(board:dict,source:str,post_id,title,location,job_url,description="",**extra)->RawJob:
    return RawJob(source_platform=source,source_id=f"{board['company']}:{post_id}",company_name=board["company"],
        job_title=title or "",location=location or "",job_url=job_url or "",description=description or "",**extra)


def greenhouse(board:dict,f:Fetcher)->list[RawJob]:
    token=board["board"]
    host="boards-api.greenhouse.io"
    data=f.get(f"https://{host}/v1/boards/{quote(token)}/jobs",params={"content":"true"},expected_host=host)
    out=[]
    for j in data.get("jobs",[]):
        loc=(j.get("location") or {}).get("name","")
        url=j.get("absolute_url","")
        if not url:continue
        out.append(build_job(board,"Greenhouse",j.get("id"),j.get("title"),loc,url,
            plaintext(j.get("content")),date_posted=iso_date(j.get("first_published")),
            date_evidence="first_published" if j.get("first_published") else None))
    return out


def lever(board:dict,f:Fetcher)->list[RawJob]:
    host="api.eu.lever.co" if board.get("region")=="eu" else "api.lever.co"
    site=board["board"]
    out=[];skip=0
    for _ in range(min(20,int(board.get("max_pages",5)))):
        batch=f.get(f"https://{host}/v0/postings/{quote(site)}",params={"mode":"json","skip":skip,"limit":100},expected_host=host)
        for j in batch:
            cat=j.get("categories") or {}
            url=j.get("hostedUrl") or j.get("applyUrl")
            if not url:continue
            desc=" ".join([j.get("descriptionPlain") or plaintext(j.get("description")),j.get("additionalPlain","") or "",plaintext(j.get("lists",[]))])
            dt=numeric_timestamp_date(j.get("createdAt"))
            out.append(build_job(board,"Lever",j.get("id"),j.get("text"),cat.get("location",""),url,
                desc,date_posted=dt,date_evidence="createdAt" if dt else None,
                employment_type=cat.get("commitment"),work_model=cat.get("workplaceType")))
        if len(batch)<100:break
        skip+=100
    return out


def ashby(board:dict,f:Fetcher)->list[RawJob]:
    host="api.ashbyhq.com"
    data=f.get(f"https://{host}/posting-api/job-board/{quote(board['board'])}",expected_host=host)
    out=[]
    for j in data.get("jobs",[]):
        if j.get("isListed") is False:continue
        url=j.get("jobUrl") or j.get("applyUrl")
        if not url:continue
        address=j.get("address") or {}
        postal=address.get("postalAddress") or {}
        country=postal.get("addressCountry") or None
        location=j.get("location","")
        secondary=j.get("secondaryLocations") or []
        locs=[location]+[s.get("location","") for s in secondary]
        country_list=[country]+[(s.get("address") or {}).get("addressCountry") for s in secondary]
        de_any=any(str(c).upper() in ("DE","DEU","GERMANY") for c in country_list if c)
        if de_any and not (country and str(country).upper() in ("DE","DEU","GERMANY")):
            idx=next((i for i,s in enumerate(secondary) if str((s.get("address") or {}).get("addressCountry","")).upper() in ("DE","DEU","GERMANY")),None)
            if idx is not None:
                location=secondary[idx].get("location",location); country="DE"
        dt=iso_date(j.get("publishedAt"))
        out.append(build_job(board,"Ashby",j.get("id") or url,j.get("title"),location,url,
            j.get("descriptionPlain") or plaintext(j.get("descriptionHtml")),country_code=country,
            date_posted=dt,date_evidence="publishedAt" if dt else None,
            employment_type=j.get("employmentType"),work_model=j.get("workplaceType"),
            remote_eligible_germany=de_any and bool(j.get("isRemote"))))
    return out


def personio(board:dict,f:Fetcher)->list[RawJob]:
    subdomain=board["board"];host=f"{subdomain}.jobs.personio.de"
    xml=f.get(f"https://{host}/xml",params={"language":"en"},expected_host=host,expect="text")
    root=SafeET.fromstring(xml)
    positions=root.findall(".//position")

    def description(p)->str:
        # Some tenants do not populate English XML descriptions. Do not
        # invent a description or treat empty text as an experience waiver.
        content=" ".join(x.findtext("value","") or "" for x in p.findall("./jobDescriptions/jobDescription"))
        return plaintext(content)

    german={}
    if any(not description(p) for p in positions):
        # A second PUBLIC employer XML request, never an unapproved HTML crawl.
        xml_de=f.get(f"https://{host}/xml",params={"language":"de"},expected_host=host,expect="text")
        root_de=SafeET.fromstring(xml_de)
        german={p.findtext("id"):description(p) for p in root_de.findall(".//position") if p.findtext("id")}
    out=[]
    for p in positions:
        identity=p.findtext("id")
        template=board.get("apply_url_template")
        if not identity or not template:continue
        url=template.replace("{id}",identity)
        if urlsplit(url).hostname!=host:raise SourceError("Personio apply template must belong to the configured board")
        desc=description(p) or german.get(identity,"")
        out.append(build_job(board,"Personio",identity,p.findtext("name"),p.findtext("office"),url,
            desc,date_posted=None,date_evidence="XML omits reliable first-published date",
            employment_type=p.findtext("employmentType"),job_level=p.findtext("seniority"),
            structured_experience_years=p.findtext("yearsOfExperience"),
            work_model=p.findtext("schedule")))
    return out


def parse_workday_url(url:str)->tuple[str,str,str,str]:
    p=urlsplit(url)
    m=re.fullmatch(r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com",p.hostname or "")
    if p.scheme!="https" or not m:raise SourceError("Workday must use a verified *.wdN.myworkdayjobs.com career URL")
    parts=[x for x in p.path.strip("/").split("/") if x]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}",parts[0]):parts.pop(0)
    if not parts:raise SourceError("Workday career site name missing")
    return (p.hostname,m.group(1),parts[0],f"https://{p.hostname}/{parts[0]}")


def workday(board:dict,f:Fetcher)->list[RawJob]:
    host,tenant,site,public_url=parse_workday_url(board["career_url"])
    api=f"https://{host}/wday/cxs/{tenant}/{site}"
    if not f.robots_allowed(f"https://{host}/{site}/"):
        raise SourceError("Workday public career site disallowed or robots.txt unavailable")
    out=[];seen=set()
    # Avoid wide, expensive crawling: keyword queries, few pages, only details of candidate titles.
    queries=board.get("queries",["SAP Basis","SAP HANA","data engineer"])
    for q in queries:
        offset=0;total=None
        for _ in range(min(15,int(board.get("max_pages_per_query",3)))):
            data=f.post(f"{api}/jobs",json={"appliedFacets":{},"limit":20,"offset":offset,"searchText":q},expected_host=host)
            batch=data.get("jobPostings") or []
            if total is None:total=int(data.get("total",0))
            for j in batch:
                if len(out) >= max(1,int(board.get("max_detail_jobs",999))):
                    return out
                path=j.get("externalPath") or ""
                if not path.startswith("/job/") or path in seen or not (ROLE.search(j.get("title","")) or SAP_BASIS_ROLE.search(j.get("title",""))):continue
                seen.add(path)
                detail=f.get(f"{api}{path}",expected_host=host)
                info=detail.get("jobPostingInfo") or detail
                title=info.get("title") or j.get("title","")
                desc=plaintext(info.get("jobDescription") or "")
                raw_location=info.get("location") or j.get("locationsText","")
                posted=iso_date(info.get("postedOn"))  # relative labels deliberately produce null
                out.append(build_job(board,"Workday",info.get("jobReqId") or path,title,raw_location,
                    public_url+path,desc,date_posted=posted,
                    date_evidence="postedOn exact" if posted else "relative/unknown postedOn",
                    employment_type=info.get("timeType"),work_model=info.get("remoteType")))
            offset+=20
            if not batch or offset>=min(2000,total or 0):break
    return out


def smartrecruiters(board:dict,f:Fetcher)->list[RawJob]:
    """Official, public SmartRecruiters Posting API, with per-company verification.

    Keep requests small: query known target terms, inspect *titles* before
    downloading description details, never use private hiring APIs.
    """
    company_id=board["board"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}",company_id):
        raise SourceError("Invalid SmartRecruiters public board identifier")
    host="api.smartrecruiters.com"
    api=f"https://{host}/v1/companies/{quote(company_id)}/postings"
    queries=board.get("queries",["SAP Basis","SAP NetWeaver","Data Engineer"])
    out=[];seen=set()
    for query in queries[:8]:
        offset=0
        for _ in range(min(3,int(board.get("max_pages_per_query",1)))):
            data=f.get(api,params={"q":query,"limit":100,"offset":offset},expected_host=host)
            content=data.get("content") or []
            for j in content:
                if len(out) >= max(1,int(board.get("max_detail_jobs",999))):
                    return out
                identity=str(j.get("id") or "")
                title=j.get("name") or ""
                if not identity or identity in seen or not (ROLE.search(title) or SAP_BASIS_ROLE.search(title)):
                    continue
                seen.add(identity)
                location=j.get("location") or {}
                country=str(location.get("country") or "").upper()
                if country and country not in ("DE","IN"):
                    continue
                detail=f.get(f"{api}/{quote(identity)}",expected_host=host)
                official=detail.get("postingUrl") or detail.get("applyUrl")
                if not official or urlsplit(official).hostname!="jobs.smartrecruiters.com":
                    # Keep only employer-specific public ATS posting links.
                    continue
                details=detail.get("jobAd") or {}
                sections=details.get("sections") or {}
                description=" ".join(plaintext((sections.get(k) or {}).get("text")) for k in
                    ("jobDescription","qualifications","additionalInformation"))
                address=detail.get("location") or location
                country=str(address.get("country") or country).upper()
                location_text=address.get("fullLocation") or ", ".join(
                    x for x in (address.get("city"),{"DE":"Germany","IN":"India"}.get(country)) if x)
                remote=bool(address.get("remote"))
                if remote and country in ("DE","IN") and not re.search("remote",location_text,re.I):
                    location_text="Remote, "+location_text
                dt=iso_date(detail.get("releasedDate") or j.get("releasedDate"))
                out.append(build_job(board,"SmartRecruiters",identity,detail.get("name") or title,
                    location_text,official,description,country_code=country or None,
                    date_posted=dt,date_evidence="releasedDate" if dt else None,
                    remote_eligible_germany=remote and country=="DE",
                    remote_eligible_india=remote and country=="IN",
                    employment_type=(detail.get("typeOfEmployment") or {}).get("label"),
                    work_model="Remote" if remote else ("Hybrid" if address.get("hybrid") else None)))
            offset+=100
            if not content or offset>=min(300,int(data.get("totalFound") or 0)):
                break
    return out


def successfactors(board:dict,f:Fetcher)->list[RawJob]:
    """Read *company-provided public XML feed*. SAP doesn't expose one universal public tenant API."""
    url=board["feed_url"];host=urlsplit(url).hostname
    if not host:raise SourceError("SuccessFactors feed URL missing")
    xml=f.get(url,expected_host=host,expect="text")
    root=SafeET.fromstring(xml)
    out=[]
    mapping=board.get("fields",{"id":"jobid","title":"title","location":"location","url":"url","description":"description","date":"date"})
    for p in list(root.findall(".//job"))+list(root.findall(".//item")):
        def value(key):return p.findtext(mapping.get(key,key),"")
        job_url=value("url")
        if not job_url or urlsplit(job_url).scheme!="https":continue
        posted=iso_date(value("date"))
        out.append(build_job(board,"SAP SuccessFactors",value("id") or job_url,value("title"),value("location"),job_url,
            plaintext(value("description")),date_posted=posted,date_evidence="XML job date" if posted else None))
    return out


def html_site(board:dict,f:Fetcher,*,headless=False)->list[RawJob]:
    """Explicit CSS selectors and robots compliance; no search engine HTML scraping."""
    url=board["url"];host=urlsplit(url).hostname
    if not host:raise SourceError("HTML career URL missing")
    if not f.robots_allowed(url):raise SourceError("robots.txt blocked or not readable")
    if headless:
        # Only the configured public employer page; never follow arbitrary offsite links.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(user_agent=f.client.headers.get("User-Agent","DataPulseJobs/0.1"))
            page.goto(url,wait_until="domcontentloaded",timeout=15000)
            markup=page.content();browser.close()
    else:
        markup=f.get(url,expected_host=host,expect="text",check_robots=True)
    soup=BeautifulSoup(markup,"html.parser");s=board["selectors"]
    out=[]
    for i,node in enumerate(soup.select(s["job"])):
        title_node=node.select_one(s["title"])
        location_node=node.select_one(s["location"])
        anchor=node.select_one(s["link"])
        if not (title_node and location_node and anchor and anchor.get("href")):continue
        link=urljoin(url,anchor["href"])
        if urlsplit(link).hostname not in (host,*board.get("approved_apply_hosts",[])):continue
        desc=plaintext(str(node))
        out.append(build_job(board,"Official Company Portal",anchor.get("data-job-id") or link,
            title_node.get_text(" ",strip=True),location_node.get_text(" ",strip=True),link,desc))
    return out

CONNECTORS={"greenhouse":greenhouse,"lever":lever,"ashby":ashby,"personio":personio,
    "workday":workday,"successfactors":successfactors,"smartrecruiters":smartrecruiters,"html":html_site}
