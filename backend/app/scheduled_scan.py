"""Authenticated, asynchronous, read-only ATS scan triggered by the existing Vercel Hobby Cron.

Deliberately has no job-writing path: promotion to production ingestion requires
reviewing real live scans and a separate explicit code change.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from .collectors.run_ingestion import ROOT, collect
from .link_checker import check_stale_jobs

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])
log = logging.getLogger("datapulse.scheduled_scan")
_lock = asyncio.Lock()


def require_scan_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("SCAN_TRIGGER_TOKEN", "")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if len(expected) < 32 or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Authenticated scan trigger required")


async def _execute_scan(pool, run_id) -> None:
    try:
        result = await collect(
            ROOT / "config" / "verified-boards.json",
            dry_run=True,
            max_boards=11,
        )
        # This is a read-only ATS scan: jobs table and bookmark table are untouched.
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
        log.info(
            "ATS read-only scan finished: attempted=%d, succeeded=%d, seen=%d, eligible=%d, failures=%d",
            result["boards_attempted"], result["boards_succeeded"],
            result["jobs_seen"], result["accepted"], len(result["errors"]),
        )
    except Exception as exc:
        log.exception("ATS read-only scan failed: %s", type(exc).__name__)
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

    return {"status": "accepted", "dry_run": True, "run_id": str(run_id)}
