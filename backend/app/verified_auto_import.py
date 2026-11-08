"""Allowlisted automatic ATS import, separate from broad read-only scans.

Only Dow's previously verified public Workday career feed is enabled in the
first production pilot. All other ATS sources remain discovery/dry-run.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from datapulse_ingestion.connectors import CONNECTORS, Fetcher
from datapulse_ingestion.profiles import normalize_for_profiles, SAP_EXPERIENCED
from .db import upsert_job
from .collectors.run_ingestion import ROOT, eligible_boards

log=logging.getLogger("datapulse.verified_auto_import")

APPROVED_COMPANY="Dow"
APPROVED_HOST="dow.wd1.myworkdayjobs.com"
MAX_ACCEPTED=5


def approved_board(registry=None):
    boards=registry if registry is not None else eligible_boards(ROOT/"config"/"verified-boards.json")
    hits=[b for b in boards if b.get("enabled") and b.get("company")==APPROVED_COMPANY
          and b.get("type")=="workday"
          and urlsplit(b.get("career_url","")).hostname==APPROVED_HOST]
    if len(hits)!=1:
        raise RuntimeError("Approved Workday board not uniquely available")
    # Scope this pilot to SAP Basis postings, one page, five detail requests.
    return {**hits[0],"queries":["SAP Basis"],"max_pages_per_query":1,"max_detail_jobs":5}


def pilot_eligibility(raw):
    """Fail closed for other employers, locations or experience levels."""
    if raw.company_name!=APPROVED_COMPANY or raw.source_platform!="Workday":
        return None
    p=urlsplit(raw.job_url or "")
    if p.scheme!="https" or p.hostname!=APPROVED_HOST or not p.path.startswith("/ExternalCareers/job/"):
        return None
    job,why=normalize_for_profiles(raw)
    if job is None or SAP_EXPERIENCED not in job.persona_tags or job.country_code not in ("DE","IN"):
        return None
    lo,hi=job.min_experience_years,job.max_experience_years
    if lo is None or not (3<=lo<=6) or (hi is not None and hi>6):
        return None
    return job


async def import_approved_jobs(pool, *,registry=None,connector=None,upsert=None)->dict:
    """Read public ATS, revalidate, and upsert at most five eligible jobs.

    Returns counts for the scheduler audit. DB write failures are propagated,
    so the scheduler reports them rather than claiming success.
    """
    board=approved_board(registry)
    def fetch():
        if connector is not None:
            return connector(board)
        f=Fetcher(timeout=8.0,delay_seconds=1.0)
        try:
            return CONNECTORS["workday"](board,f)
        finally:
            f.client.close()
    # The connector enforces per-request bounds and limits detail fetching.
    # The background scan remains responsive because the fetch runs in a thread.
    raw_jobs=await asyncio.wait_for(asyncio.to_thread(fetch),timeout=100)
    seen=set()
    report={"source":"workday:Dow","seen":len(raw_jobs),"eligible":0,
            "inserted":0,"updated":0,"skipped":0,"errors":0}
    persist=upsert or upsert_job
    for raw in raw_jobs:
        item=pilot_eligibility(raw)
        if item is None or item.source_key in seen or report["eligible"]>=MAX_ACCEPTED:
            report["skipped"]+=1
            continue
        seen.add(item.source_key)
        report["eligible"]+=1
        try:
            action,_=await persist(pool,item,raw.description)
            if action not in ("inserted","updated"):
                raise ValueError("Unexpected upsert status")
            report[action]+=1
        except Exception:
            report["errors"]+=1
            log.exception("Approved Workday job upsert failed")
    if report["errors"]:
        raise RuntimeError(f"{report['errors']} approved ATS database writes failed")
    return report
