"""Bounded public ATS/feed verification. No authentication or database writes.

Public Softgarden / SuccessFactors endpoints are *candidates*, never assumed
accessible because their hostnames match an ATS vendor. Official observed
career links and robots permission are required before any probe.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from datetime import date
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from datapulse_ingestion.models import RawJob

HTTP_TIMEOUT = 5.0
MAX_BYTES = 1_000_000
ATS_SUFFIXES = (".softgarden.io", ".softgarden.de", ".successfactors.com",
                ".successfactors.eu", ".myworkdayjobs.com")
KNOWN_STRUCTURED_PATH = re.compile(
    r"(^|/)(?:sfcareer/jobreq|odata/v2/jobreq(?:uisition)?|"
    r"api/rest/v2/jobpostings)(?:/|$)", re.I)


def public_https(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443) or host.casefold() in {"localhost", "metadata.google.internal"}:
            return False
        if host.endswith((".local", ".internal", ".localhost", ".test")):
            return False
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return True
        return ip.is_global
    except ValueError:
        return False


def _flatten_jsonld(node: Any):
    if isinstance(node, list):
        for part in node:
            yield from _flatten_jsonld(part)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from _flatten_jsonld(node["@graph"])


def parse_jsonld_jobs(html: str, page_url: str, company: str) -> list[RawJob]:
    """Parse concrete schema.org JobPosting objects from one official page."""
    if len(html.encode("utf-8")) > MAX_BYTES or not public_https(page_url):
        return []
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for script in soup.select('script[type="application/ld+json"]')[:24]:
        if not script.string or len(script.string) > 200000:
            continue
        try:
            objects = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _flatten_jsonld(objects):
            kind = item.get("@type", "")
            kinds = [kind] if isinstance(kind, str) else kind if isinstance(kind, list) else []
            if "JobPosting" not in kinds:
                continue
            title = str(item.get("title") or "").strip()
            description = BeautifulSoup(unescape(str(item.get("description") or "")),
                                        "html.parser").get_text(" ", strip=True)
            deadline = str(item.get("validThrough") or "")[:10]
            if deadline:
                try:
                    if date.fromisoformat(deadline) < date.today():
                        continue
                except ValueError:
                    continue
            if not title or not description:
                continue
            org = item.get("hiringOrganization") or {}
            org_name = org.get("name") if isinstance(org, dict) else None
            # A directory result is not permission to claim another employer.
            if org_name and re.sub(r"\W", "", str(org_name).casefold()) != re.sub(r"\W", "", company.casefold()):
                continue
            places = item.get("jobLocation") or []
            if not isinstance(places, list):
                places = [places]
            location_parts = []
            country = None
            for place in places[:4]:
                if not isinstance(place, dict):
                    continue
                address = place.get("address") or {}
                if not isinstance(address, dict):
                    continue
                candidate = address.get("addressCountry")
                if isinstance(candidate, dict):
                    candidate = candidate.get("name")
                code = str(candidate or "").upper()
                country_candidate = {"DE":"DE","GERMANY":"DE","DEUTSCHLAND":"DE",
                                     "IN":"IN","INDIA":"IN"}.get(code)
                if country_candidate:
                    country = country_candidate if not country or country == country_candidate else None
                location_parts.append(", ".join(str(address.get(k)) for k in
                    ("addressLocality","addressRegion","addressCountry") if address.get(k)))
            if not country:
                eligible = item.get("applicantLocationRequirements") or {}
                values = eligible if isinstance(eligible,list) else [eligible]
                codes = set()
                for value in values:
                    if isinstance(value,dict):
                        code = str(value.get("name","")).upper()
                        mapped = {"GERMANY":"DE","DE":"DE","INDIA":"IN","IN":"IN"}.get(code)
                        if mapped:codes.add(mapped)
                if len(codes) == 1:
                    country = next(iter(codes))
            if country not in ("DE","IN"):
                continue
            source_id = item.get("identifier")
            if isinstance(source_id, dict):
                source_id = source_id.get("value")
            target = item.get("url") or page_url
            if not isinstance(target, str) or not public_https(target):
                continue
            if urlsplit(target).hostname != urlsplit(page_url).hostname:
                # Don't trust off-site JSON-LD application redirects.
                target = page_url
            location = " / ".join(x for x in location_parts if x) or (
                "Remote Germany" if country == "DE" else "Remote India")
            remote = item.get("jobLocationType") == "TELECOMMUTE"
            jobs.append(RawJob(
                source_platform="Company_Portal",source_id=company+":"+str(source_id or target),
                company_name=company, job_title=title, location=location,
                job_url=target,description=description[:20000],country_code=country,
                date_posted=str(item.get("datePosted") or "")[:10] or None,
                employment_type=str(item.get("employmentType") or "") or None,
                remote_eligible_germany=remote and country=="DE",
                remote_eligible_india=remote and country=="IN"))
            if len(jobs)>=20:
                return jobs
    return jobs


def observed_vendor_links(html: str, page_url: str) -> list[dict]:
    """Never synthesize a slug; only vendor hostnames seen in actual links."""
    if not public_https(page_url):
        return []
    soup=BeautifulSoup(html[:MAX_BYTES],"html.parser")
    urls=set();out=[]
    for el in soup.select("a[href],iframe[src],link[href]")[:400]:
        raw=el.get("href") or el.get("src")
        if not raw:continue
        url=urljoin(page_url,raw)
        if not public_https(url):continue
        parsed=urlsplit(url);host=(parsed.hostname or "").lower()
        kind=None
        if host.endswith((".softgarden.io",".softgarden.de")):kind="softgarden"
        elif host.endswith((".successfactors.com",".successfactors.eu")):kind="successfactors"
        elif host.endswith(".myworkdayjobs.com"):kind="workday"
        elif (host == urlsplit(page_url).hostname and
              KNOWN_STRUCTURED_PATH.search(parsed.path)):kind="structured"
        if kind and (kind,host) not in urls:
            urls.add((kind,host))
            out.append({"type":kind,"url":url,"origin":"observed_career_link"})
        if len(out)>=10:break
    return out


class BoundedVerifier:
    def __init__(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
        self.client=client
        self.semaphore=semaphore
        self._robots:dict[str,RobotFileParser|None]={}
        self._locks:dict[str,asyncio.Lock]={}

    async def _get(self,url:str) -> httpx.Response:
        async with self.semaphore:    # global max simultaneous outbound requests
            return await self.client.get(url,timeout=HTTP_TIMEOUT,follow_redirects=False)

    async def robots_allowed(self,url:str) -> bool:
        if not public_https(url):return False
        parsed=urlsplit(url)
        base=parsed.scheme+"://"+parsed.netloc
        lock=self._locks.setdefault(base,asyncio.Lock())
        async with lock:
            if base not in self._robots:
                try:
                    r=await self._get(base+"/robots.txt")
                    if r.status_code!=200 or "user-agent:" not in r.text.lower():
                        self._robots[base]=None
                    else:
                        parser=RobotFileParser()
                        parser.parse(r.text.splitlines())
                        self._robots[base]=parser
                except (httpx.HTTPError,ValueError):
                    self._robots[base]=None
        rules=self._robots[base]
        return rules is not None and rules.can_fetch("DataPulseJobs",url)

    async def fetch(self,url:str,content_types:tuple[str,...]=("text/html",)) -> tuple[str|None,str]:
        if not await self.robots_allowed(url):return None,"robots_unverified_or_disallowed"
        try:
            r=await self._get(url)
        except httpx.TimeoutException:return None,"http_timeout_5s"
        except httpx.HTTPError:return None,"http_error"
        if r.status_code in (401,403):return None,"authentication_or_access_required"
        if r.status_code==429:return None,"rate_limited_no_retry"
        if r.is_redirect:return None,"redirect_unverified"
        if r.status_code!=200:return None,"http_"+str(r.status_code)
        if len(r.content)>MAX_BYTES:return None,"response_too_large"
        if not any(x in r.headers.get("content-type","").lower() for x in content_types):
            return None,"unexpected_content_type"
        return r.text,"ok"

    async def verify_vendor(self,candidate:dict) -> dict:
        """Candidate is only verified with public machine-readable vacancy rows."""
        kind=candidate.get("type");url=candidate.get("url","")
        result={"type":kind,"observed_url":url,"status":"unverified","jobs":[]}
        if not public_https(url):return result
        host=urlsplit(url).hostname or ""
        if kind=="softgarden":
            # Probe only the SAME observed Softgarden host.
            candidates=["https://"+host+"/api/rest/v2/jobpostings",url]
        elif kind=="successfactors":
            # OData is frequently auth-only. Never guess tenant API paths.
            candidates=[url] if KNOWN_STRUCTURED_PATH.search(urlsplit(url).path) else []
        elif kind=="workday":
            # Existing Workday connector owns this site's public career API.
            result["status"]="workday_public_site_candidate"
            return result
        elif kind=="structured":
            candidates=[url]
        else:
            return result
        if not candidates:
            result["status"]="no_observed_public_feed_url"
            return result
        for endpoint in dict.fromkeys(candidates):
            body,status=await self.fetch(endpoint,("application/json","application/ld+json","text/json"))
            if body is None:
                result["status"]=status
                continue
            try:payload=json.loads(body)
            except (json.JSONDecodeError,ValueError):
                result["status"]="invalid_json"
                continue
            records=payload if isinstance(payload,list) else (
                payload.get("jobpostings") or payload.get("jobs") or
                payload.get("results") or payload.get("d",{}).get("results")
                if isinstance(payload,dict) else [])
            if not isinstance(records,list) or not records:
                result["status"]="unrecognized_or_empty_job_schema"
                continue
            if not any(isinstance(x,dict) and
                       (x.get("title") or x.get("jobTitle") or x.get("name")) and
                       (x.get("id") or x.get("jobId") or x.get("jobReqId"))
                       for x in records[:20]):
                result["status"]="unrecognized_job_schema"
                continue
            result["status"]="verified_public_json_jobs"
            result["endpoint"]=endpoint
            result["jobs"]=records[:20]
            return result
        return result
