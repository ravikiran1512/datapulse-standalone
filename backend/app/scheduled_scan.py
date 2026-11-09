"""Authenticated Vercel Cron trigger: bounded approved imports + read-only discovery.

The approved importer is server-side, opt-in, and always checks official links.
The broader partner/source discovery remains read-only.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from .collectors.run_ingestion import ROOT, collect, eligible_boards
from .link_checker import check_stale_jobs
from .db import upsert_job
from .collectors.discover_partners import discover

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])
log = logging.getLogger("datapulse.scheduled_scan")
_lock = asyncio.Lock()


async def persist_approved_batch(pool,records):
    """Persist only prefiltered, live-link-verified candidates on this server."""
    result={'inserted':0,'updated':0}
    for raw,normalized in records:
        action,job_id=await upsert_job(pool,normalized,raw.description)
        if action not in ('inserted','updated'):
            raise RuntimeError('Unexpected approved job upsert result')
        # collect() has already confirmed the exact official URL by HEAD+GET.
        # Persist that verification before it can appear in the public API;
        # uncertain/unchecked listings remain hidden until revalidated.
        status=await pool.execute('''UPDATE datapulse.jobs SET
            link_status='live',link_failures=0,last_link_checked_at=now(),
            link_last_confirmed_at=now(),
            link_detail='Official ATS link reverified during approved ingestion'
            WHERE id=$1 AND official_apply_url=$2''',UUID(job_id),normalized.job_url)
        if status!='UPDATE 1':
            raise RuntimeError('Verified ATS URL did not match the persisted job')
        result[action]+=1
    return result


def require_scan_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("SCAN_TRIGGER_TOKEN", "")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if len(expected) < 32 or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Authenticated scan trigger required")


async def _execute_scan(pool, run_id) -> None:
    try:
        approved_error=None
        registry=ROOT / "config" / "verified-boards.json"
        if os.getenv('ENABLE_VERIFIED_AUTO_IMPORT','false').lower()=='true':
            # Record production imports separately from the broad read-only
            # scan. The previous pilot conflated their accepted-job totals.
            import_run_id=await pool.fetchval(
                'INSERT INTO datapulse.ingestion_runs(dry_run) VALUES(FALSE) RETURNING id')
            try:
                async def save(records):
                    return await persist_approved_batch(pool,records)
                imported=await collect(registry,dry_run=False,approved_only=True,
                                       persist=save)
                if imported['accepted']!=imported['inserted']+imported['updated']:
                    raise RuntimeError('Approved import did not persist every eligible job')
                await pool.execute(
                    '''UPDATE datapulse.ingestion_runs
                       SET completed_at=now(),sources_attempted=$2,
                           sources_succeeded=$3,jobs_seen=$4,accepted=$5,
                           inserted=$6,updated=$7,rejected=$8::jsonb,
                           failures=$9::jsonb
                       WHERE id=$1''',
                    import_run_id,imported['boards_attempted'],imported['boards_succeeded'],
                    imported['jobs_seen'],imported['accepted'],imported['inserted'],
                    imported['updated'],json.dumps(imported['rejected']),json.dumps(imported['errors']))
                if imported['errors'] or imported['boards_succeeded']==0:
                    approved_error='ApprovedSourceFailure'
                    log.error('Approved ATS import had source errors: %s',imported['errors'])
                log.info('Approved ATS sources imported: %s',imported)
            except Exception as exc:
                approved_error=type(exc).__name__
                await pool.execute('''UPDATE datapulse.ingestion_runs
                    SET completed_at=now(),failures=$2::jsonb WHERE id=$1''',
                    import_run_id,json.dumps({'approved_import':approved_error}))
                log.exception('Approved ATS import failed; other sources remain read-only')
        board_count=len(eligible_boards(registry))
        board_offset=(date.today().toordinal()*11)%board_count if board_count else 0
        result = await collect(
            registry,
            dry_run=True,
            max_boards=11,
            board_offset=board_offset,
            expand_registry=True,
        )
        # Non-approved registry sources never acquire a write path.
        if approved_error:
            result['errors']['approved_import']=approved_error
        await pool.execute(
            """UPDATE datapulse.ingestion_runs
                SET completed_at=now(), sources_attempted=$2, sources_succeeded=$3,
                    jobs_seen=$4, accepted=$5, rejected=$6::jsonb, failures=$7::jsonb
                WHERE id=$1""",
            run_id,
            result["boards_attempted"],
            result["boards_succeeded"],
            result["jobs_seen"],
            result["accepted"],
            json.dumps(result["rejected"]),
            json.dumps(result["errors"]),
        )
        # Stale-link maintenance only; does not insert new jobs. Limit per day.
        # Disable by setting ENABLE_STALE_CHECKS=false in the Render environment.
        if os.getenv('ENABLE_STALE_CHECKS','true').lower() == 'true':
            try:
                link_report=await check_stale_jobs(pool,limit=25)
                log.info('Stale-link maintenance: %s',link_report)
            except Exception:
                log.exception('Stale-link maintenance failed; listings preserved')
        # Once weekly, discover potential partner career feed links without
        # guessing slugs, inserting jobs, or adding new cron schedules.
        if date.today().weekday()==6 and os.getenv('ENABLE_PARTNER_DISCOVERY','true').lower()=='true':
            try:
                targets=json.loads((ROOT/'config'/'partner_targets.json').read_text())
                leads=await discover(targets,max_targets=3)
                log.info('Weekly partner directory leads: %s',
                         [(x['name'],len(x['candidates']),x['status']) for x in leads])
            except Exception:
                log.exception('Partner discovery unavailable; ingestion unaffected')
        log.info(
            "ATS discovery scan finished: attempted=%d, succeeded=%d, seen=%d, eligible=%d, failures=%d",
            result["boards_attempted"], result["boards_succeeded"],
            result["jobs_seen"], result["accepted"], len(result["errors"]),
        )
    except Exception as exc:
        log.exception("ATS scheduled scan failed: %s", type(exc).__name__)
        try:
            await pool.execute(
                """UPDATE datapulse.ingestion_runs SET completed_at=now(),
                     failures=$2::jsonb WHERE id=$1""",
                run_id, json.dumps({"runner": type(exc).__name__}),
            )
        except Exception:
            log.exception("Could not record failed ATS run")


@router.post("/scan", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_scan_token)])
async def start_readonly_scan(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    async with _lock:
        running = getattr(request.app.state, "readonly_scan_task", None)
        if running is not None and not running.done():
            return {"status": "already_running", "dry_run": True}

        run_id = await pool.fetchval(
            "INSERT INTO datapulse.ingestion_runs (dry_run) VALUES (TRUE) RETURNING id"
        )
        task = asyncio.create_task(_execute_scan(pool, run_id), name=f"datapulse-dry-run-{run_id}")
        request.app.state.readonly_scan_task = task

    return {"status": "accepted", "dry_run": True,
            "automatic_approved_import": os.getenv('ENABLE_VERIFIED_AUTO_IMPORT','false').lower()=='true',
            "run_id": str(run_id)}
