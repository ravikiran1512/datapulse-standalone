# DataPulse controlled production importer

A single verified **Dow Workday** employer feed is allowlisted for automatic daily
ingestion. All other employer boards remain in read-only scan mode.

## Safety controls

- Enabled only with Render server-side `ENABLE_VERIFIED_AUTO_IMPORT=true`.
- Triggered by the existing protected Vercel Cron -> Render scan endpoint.
  It is not a public unauthenticated import endpoint.
- Only the configured **Dow** Workday career domain and enabled board.
- Queries only `SAP Basis`, one Workday search page, at most five job details.
- Rechecks job identity, official HTTPS apply URL, employer identity,
  Germany/India eligibility, SAP Basis experienced persona and 3–6 years
  minimum-required experience.
- Upserts into existing `datapulse.jobs` with its normalized
  employer/title/location uniqueness constraint; preserves stable job UUIDs,
  source audit rows and saved jobs.
- The daily stale-link checker remains active and requires repeated evidence
  before marking a job inactive.
- Every production run records the opt-in mode and insert/update totals in
  `datapulse.ingestion_runs`.
- All other employer sources remain dry-run only. This pilot does not
  activate SAP and Databricks partner-directory write paths.

## Rollback

Set `ENABLE_VERIFIED_AUTO_IMPORT=false` in Render environment variables.
The daily broad scan stays read-only, while previously imported jobs are
preserved. Do not delete or truncate jobs to disable the automation.

## Tests

```bash
PYTHONPATH=backend python -m pytest -q backend/tests/test_verified_auto_import.py
```

The first manual, verified Workday import succeeded (1 inserted, 0 errors).
Public `/api/v1/jobs` and filtered SAP Basis Germany/India endpoints returned
the imported job as expected before scheduled pilot activation.
