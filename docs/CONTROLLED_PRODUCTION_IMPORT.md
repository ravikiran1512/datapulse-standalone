# DataPulse approved-source automatic ingestion

Two distinct schedules are configured for 05:17 UTC daily:

1. **GitHub Actions** (`.github/workflows/ingest_jobs.yml`) is the intended
   primary importer. It tests the backend and invokes the Python collector with
   `--approved-only --dry-run=false --apply`. It writes only through the
   authenticated FastAPI `/api/v1/internal/ingest` endpoint.
2. **Vercel Cron** (`frontend/vercel.json`) triggers the Render API's scan and
   stale-link maintenance. The older Dow-only importer is still available
   behind `ENABLE_VERIFIED_AUTO_IMPORT=true` as a fallback until the first
   GitHub Actions production run has been observed and verified. Once proven,
   set `ENABLE_VERIFIED_AUTO_IMPORT=false` to leave only one production writer.

The GitHub schedule is best-effort; examine run history and failures. Do not
assume a scheduled run succeeded merely because the workflow exists.

## Explicit production approvals

Only registry entries with **both** `enabled: true` and
`auto_import_approved: true` are eligible for production writes. Current
bounded allowlist: GAMBIT Consulting (Personio), Nagarro (SmartRecruiters), Dow
(Workday), and STATWORX (Personio). Other enabled ATS feeds remain read-only.

Every automatically imported job must additionally satisfy:

- A configured employer name, ATS type, exact approved posting hostname/path.
- All three existing profile-specific country/experience/role constraints.
- For junior Data Engineering, the English requirement, technical tags and
  >=70% weighted technical match must remain intact.
- A live, direct employer ATS application URL confirmed by bounded HEAD+GET;
  inaccessible, ambiguous, closed, or redirected-to-unapproved links are skipped.
- A maximum of five accepted jobs per approved board by default (board config
  can set `max_auto_import`, capped at 10) and 25 total per daily run.
- No sample fixture may be used for production writes. A job seen twice in the
  same run is not submitted twice.

The API independently re-runs profile validation and performs normalized
company/title/location UPSERTs with stable job IDs and source provenance.
Closed links are only deactivated after two confirmed checks; rows and user
bookmarks are never deleted. A repeated import of the same official URL does
not discard a previously confirmed `live` status.

The employer's prose takes priority when an entry-level label conflicts with
an explicit multi-year experience requirement. For example, the live GAMBIT
SAP Basis Consultant advertises *mehrjährige Erfahrung* despite Personio
`entry-level / 1-2` metadata; it is excluded from the junior/student profile.

## GitHub Actions secrets (repository settings)

The scheduled job requires two existing GitHub Actions repository secrets:

- `DATAPULSE_API_BASE_URL`: the existing HTTPS Render API origin.
- `DATAPULSE_INGEST_TOKEN`: the same long server-side `INGEST_TOKEN` already
  configured on Render. This value must never be exposed in the frontend,
  committed, included in artifacts, or pasted into issue comments.

If either secret is absent, the job **fails closed** before attempting any
production import. Configure secrets in GitHub's protected repository settings;
do not print or copy their values into logs.

## Verification and cutover checklist

1. Locally run `PYTHONPATH=backend python -m pytest -q backend/tests`.
2. Preview public sources without writes:
   `cd backend && python -m app.collectors.run_ingestion --approved-only --dry-run=true --verify-links`.
3. Run `Daily Approved ATS Job Ingestion` using `workflow_dispatch` in the
   GitHub Actions UI. Verify the job succeeds, retrieve the bounded JSON
   artifact and check that `inserted + updated` equals `accepted`.
4. Query Supabase `datapulse.ingestion_runs`, inspect the exact imported job
   records, and verify the live DataPulse UI shows them under the right preset.
5. Once the GitHub worker is proven, disable only the old Dow pilot with
   `ENABLE_VERIFIED_AUTO_IMPORT=false` in Render. Keep Vercel's stale-link
   maintenance enabled. Watch at least one subsequent scheduled run.

### Rollback

Revert the GitHub Actions workflow to `--dry-run=true` without `--apply` (or
temporarily disable the scheduled workflow). This stops new imports and does
not delete any job or saved application. Re-enable the bounded Dow Vercel pilot
only if needed; do not run two competing production writers by default.

### Monitoring

Every GitHub run prints a JSON report and retains `datapulse-approved-ats-report`
for 14 days. Failures cause a nonzero workflow result. The backend ingestion
log records accepted/rejected/inserted/updated counts; the public API exposes
the latest completed import timestamp. GitHub failure notifications depend on
repository notification settings; no external alert integration is configured.
