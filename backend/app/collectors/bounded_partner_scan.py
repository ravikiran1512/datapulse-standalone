"""Concurrent partner directory preproduction dry-run. Never opens a database.

Use official, observed career links and verified ATS boards only. One shared
asyncio.Semaphore(10) limits outbound requests; each request times out at 5s.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

from datapulse_ingestion.connectors import CONNECTORS, Fetcher
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import (
    SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR, SAP_BASIS_ROLE,
    combine_structured_experience, parse_experience, country_of,
    normalize_for_profiles,
)
from datapulse_ingestion.policy import ROLE, JUNIOR, SENIOR

from .ats_resolver import (
    BoundedVerifier, HTTP_TIMEOUT, observed_vendor_links,
    parse_jsonld_jobs, public_https,
)
from .partner_directories import (
    ats_board_from_link, ats_links, canonical_name, directory_evidence,
    directory_url, SAP_SYSTEM_ROLE, STUDENT_ROLE, EXECUTIVE_ROLE,
    select_targets,
)

PROFILES=(SAP_EXPERIENCED,SAP_STUDENT,DE_JUNIOR)
SOURCES=("sap_partners","databricks_partners","softgarden","successfactors")


def strict_preproduction_job(raw:RawJob)->tuple[tuple[str,...],str,dict]:
    """The requested PREPRODUCTION boundaries (SAP 3-6, DE <=2, students exempt)."""
    country=country_of(raw)
    if country not in ("DE","IN"):
        return (),"country_unverified",{}
    exp=combine_structured_experience(raw,parse_experience(raw.description))
    title=raw.job_title or ""
    contract=(raw.employment_type or "")+" "+title
    details={"country":country,"min_experience_years":exp.minimum,
             "max_experience_years":exp.maximum,"experience_evidence":exp.evidence}
    if EXECUTIVE_ROLE.search(title):
        return (),"executive_or_principal",details
    basis=bool(SAP_BASIS_ROLE.search(title) or
               (SAP_SYSTEM_ROLE.search(title) and STUDENT_ROLE.search(contract)))
    if basis:
        student=bool(STUDENT_ROLE.search(contract))
        junior=bool(JUNIOR.search(title) or re.search(r"\b(?:junior|entry[- ]level)\b",str(raw.job_level or ""),re.I))
        if student or junior:
            if country=="DE":
                return (SAP_STUDENT,),"accepted_sap_student_or_entry",details
            return (),"sap_entry_outside_germany",details
        if exp.minimum is None or exp.maximum is None:
            return (),"sap_3_to_6_experience_unverified",details
        if 3<=exp.minimum<=exp.maximum<=6:
            return (SAP_EXPERIENCED,),"accepted_sap_experienced",details
        return (),"sap_outside_3_to_6_years",details
    if not ROLE.search(title):
        return (),"unrelated_role",details
    if country!="DE":
        return (),"data_engineering_outside_germany",details
    if SENIOR.search(title) or re.search(r"\b(?:lead|manager|principal)\b",title,re.I):
        return (),"senior_de_role",details
    if exp.minimum is not None and exp.minimum>=3:
        return (),"de_3_plus_years",details
    if exp.maximum is not None and exp.maximum>2:
        return (),"de_3_plus_years",details
    if exp.minimum is None and not STUDENT_ROLE.search(contract):
        return (),"de_experience_unverified",details
    if exp.minimum is not None and exp.maximum is None and exp.minimum==2 and not (
        JUNIOR.search(title) or STUDENT_ROLE.search(contract)):
        return (),"de_2_plus_unbounded_review",details
    # Keep pre-existing skills, language and minimum fit-score guards.
    normalized,reason=normalize_for_profiles(raw)
    if not normalized or DE_JUNIOR not in normalized.persona_tags:
        return (),"de_quality_"+reason,details
    return (DE_JUNIOR,),"accepted_de_entry",details


def _source_ecosystems(sources:set[str])->set[str]:
    return ({"sap"} if "sap_partners" in sources else set()) | (
        {"databricks"} if "databricks_partners" in sources else set())


async def scan_bounded(
    targets:list[dict], verified_boards:list[dict], *,
    sources:set[str], profiles:tuple[str,...]=PROFILES,
    max_partners:int=16, max_feeds:int=16,
    request_client:httpx.AsyncClient|None=None,
    connector_runner=None,
)->dict:
    if not sources or not sources<=set(SOURCES):
        raise ValueError("Unsupported --sources value")
    ecosystems=_source_ecosystems(sources)
    if not ecosystems:
        # Vendor-only verification still needs the configured employer domains.
        ecosystems={"sap","databricks"}
    selected=select_targets(targets,ecosystems,min(max(max_partners,0),32))
    semaphore=asyncio.Semaphore(10)
    own_client=request_client is None
    client=request_client or httpx.AsyncClient(
        headers={"User-Agent":"DataPulseJobs/0.1 (public read-only ATS verifier)"},
        timeout=httpx.Timeout(HTTP_TIMEOUT),follow_redirects=False,
        limits=httpx.Limits(max_connections=10,max_keepalive_connections=6))
    verifier=BoundedVerifier(client,semaphore)
    directory_cache:dict[str,tuple[str|None,str]]={}
    directory_locks:dict[str,asyncio.Lock]={}
    cap_lock=asyncio.Lock()
    cap_used=0
    reason_counts=Counter()
    counts=Counter()
    samples=[]
    candidate_counts=Counter()
    source_errors=Counter()
    # This returns data, never writes jobs/saved jobs/scan records.
    async def directory_html(url):
        if url not in directory_locks:
            directory_locks[url]=asyncio.Lock()
        async with directory_locks[url]:
            if url not in directory_cache:
                directory_cache[url]=await verifier.fetch(url,("text/html","application/xhtml"))
        return directory_cache[url]

    async def one(target):
        nonlocal cap_used
        name=target["name"]
        ecosystem=target["ecosystem"]
        row={"company":name,"ecosystem":ecosystem,
             "directory_status":"unverified",
             "directory_tier":None,"career_status":"unverified",
             "ats_candidates":[],"vendor_checks":[],"feed_status":"unverified",
             "jobs_seen":0,"accepted":0,"accepted_profiles":[],
             "errors":[]}
        try:
            directory=directory_url(ecosystem,name)
            html,status=await directory_html(directory)
            row["directory_status"] = (
                "name_visible_without_tier_verification"
                if html is not None and directory_evidence(html,name)
                else "dynamic_or_name_not_visible"
                if html is not None else status)
            career=target.get("career_page","")
            if not public_https(career):
                row["career_status"]="invalid_unverified_url"
                return row
            body,status=await verifier.fetch(career,("text/html","application/xhtml"))
            row["career_status"]=status
            found=[]
            posting_jobs=[]
            if body:
                found=ats_links(body,career)
                row["ats_candidates"]=found[:12]
                posting_jobs=parse_jsonld_jobs(body,career,name)
            # When an observed career URI is itself a supported ATS board,
            # it is not inferred from a brand or directory membership.
            own_board=ats_board_from_link(career,name)
            if own_board:
                found.insert(0,{"url":career,"type":own_board["type"]})
            vendor=observed_vendor_links(body or "",career)
            if own_board and own_board["type"] in ("workday",):
                vendor.append({"type":"workday","url":career})
            if body and "softgarden" in sources or body and "successfactors" in sources:
                for v in vendor[:5]:
                    if v["type"] not in sources:
                        continue
                    verified=await verifier.verify_vendor(v)
                    status=verified["status"]
                    row["vendor_checks"].append({"type":v["type"],"status":status,
                                                 "observed_url":v["url"]})
                    candidate_counts[v["type"]]+=1
                    if status=="verified_public_json_jobs":
                        counts["public_vendor_feeds_validated"]+=1
                        # A public JSON response does not by itself prove a
                        # full job posting; preserve for schema review.
            candidates=[]
            for board in verified_boards:
                if (board.get("enabled") and
                        canonical_name(board.get("company",""))==canonical_name(name)):
                    candidates.append((dict(board),"existing_verified_registry"))
            for found_link in found:
                board=ats_board_from_link(found_link["url"],name)
                if not board:
                    continue
                key=(board["type"],board.get("board",board.get("career_url")))
                if not any(key==(b["type"],b.get("board",b.get("career_url")))
                           for b,_ in candidates):
                    candidates.append((board,"observed_official_career_link"))
            chosen=candidates[:1]   # one ATS connector maximum per employer
            if not chosen and posting_jobs:
                row["feed_status"]="verified_jobposting_jsonld"
                counts["jsonld_pages_verified"]+=1
            if chosen:
                async with cap_lock:
                    allowed=cap_used<max_feeds
                    if allowed:cap_used+=1
                if not allowed:
                    row["feed_status"]="skipped_feed_cap"
                else:
                    board,origin=chosen[0]
                    board={**board,"enabled":True}
                    typ=board.get("type","")
                    if typ=="workday":
                        board.update(queries=["SAP Basis","Data Engineer"],
                                     max_pages_per_query=1,max_detail_jobs=3)
                    elif typ=="smartrecruiters":
                        board.update(queries=["SAP Basis","Data Engineer"],
                                     max_pages_per_query=1,max_detail_jobs=3)
                    elif typ=="lever":
                        board.update(max_pages=1)
                    if typ not in CONNECTORS:
                        row["feed_status"]="no_public_connector"
                    else:
                        try:
                            def invoke():
                                if connector_runner is not None:
                                    return connector_runner(board)
                                f=Fetcher(timeout=HTTP_TIMEOUT,delay_seconds=.3)
                                try:return CONNECTORS[typ](board,f)
                                finally:f.client.close()
                            # A whole synchronous connector occupies one of the
                            # ten global outbound slots for its bounded lifetime.
                            async with semaphore:
                                fetched=await asyncio.wait_for(asyncio.to_thread(invoke),timeout=25)
                            row["feed_status"]="verified_public_ats"
                            row["feed_type"]=typ
                            row["feed_origin"]=origin
                            posting_jobs.extend(fetched[:300])
                            counts["feeds_validated"]+=1
                        except asyncio.TimeoutError:
                            row["feed_status"]="employer_budget_exceeded"
                            source_errors["employer_budget_exceeded"]+=1
                        except Exception as exc:
                            row["feed_status"]="feed_failed"
                            row["errors"].append(type(exc).__name__)
                            source_errors[type(exc).__name__]+=1
            row["jobs_seen"]=len(posting_jobs)
            for raw in posting_jobs:
                matched,reason,experience=strict_preproduction_job(raw)
                selected_profiles=tuple(x for x in matched if x in profiles)
                if not selected_profiles:
                    reason_counts[reason if not matched else "outside_selected_profiles"]+=1
                    continue
                row["accepted"]+=1
                counts["accepted"]+=1
                row["accepted_profiles"].extend(x for x in selected_profiles if x not in row["accepted_profiles"])
                for x in selected_profiles:
                    counts[x]+=1
                if len(samples)<12:
                    samples.append({
                        "company":raw.company_name,"title":raw.job_title,
                        "location":raw.location,"country":experience.get("country"),
                        "profile":list(selected_profiles),"experience_min":experience.get("min_experience_years"),
                        "experience_max":experience.get("max_experience_years"),
                        "official_url":raw.job_url})
            return row
        except asyncio.TimeoutError:
            row["career_status"]="employer_timeout_30s"
            source_errors["employer_timeout_30s"]+=1
            return row
        except Exception as exc:
            row["errors"].append(type(exc).__name__)
            source_errors[type(exc).__name__]+=1
            return row

    try:
        # A larger company set is safe: 10 shared HTTP slots, max two per host.
        rows=await asyncio.gather(*(
            asyncio.wait_for(one(t),timeout=35) for t in selected),
            return_exceptions=True)
        companies=[]
        for target,res in zip(selected,rows):
            if isinstance(res,Exception):
                companies.append({"company":target["name"],"ecosystem":target["ecosystem"],
                                  "directory_status":"error","feed_status":"failed",
                                  "error":type(res).__name__,"jobs_seen":0,"accepted":0})
                source_errors[type(res).__name__]+=1
            else:
                companies.append(res)
        report={
            "timestamp_utc":datetime.now(timezone.utc).isoformat(),
            "source":"partner_directories",
            "sources_requested":sorted(sources),
            "target_profiles":list(profiles),
            "dry_run":True,"production_writes_enabled":False,
            "concurrency_limit":10,"per_host_limit":2,"http_timeout_seconds":HTTP_TIMEOUT,
            "employer_budget_seconds":35,
            "targets_configured":len(targets),"targets_checked":len(companies),
            "directory_names_visible":sum(x.get("directory_status")=="name_visible_without_tier_verification" for x in companies),
            "feeds_attempted":cap_used,
            "feeds_validated":counts["feeds_validated"],
            "verified_public_vendor_feeds":counts["public_vendor_feeds_validated"],
            "jsonld_pages_verified":counts["jsonld_pages_verified"],
            "vendor_candidates":dict(candidate_counts),
            "jobs_seen":sum(x.get("jobs_seen",0) for x in companies),
            "accepted":counts["accepted"],
            "per_profile":{p:counts[p] for p in profiles},
            "rejections":dict(reason_counts),"errors":dict(source_errors),
            "companies":companies,"samples":samples,
            "inserted":0,"updated":0,
            "notes":["Partner tier remains unverified until a specific official directory profile confirms it.",
                     "401/403/429 responses are not bypassed or retried.",
                     "Employer career pages and public ATS feeds are not necessarily independent directory-membership proof.",
                     "No database client, ingestion API key or job-writing path is used."],
        }
        return report
    finally:
        if own_client:await client.aclose()
