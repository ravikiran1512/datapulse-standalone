# DataPulse approved-source automatic ingestion

There is exactly **one production writer**:

1. **Vercel Cron** (`frontend/vercel.json`) targets 05:17 UTC daily and triggers
   the authenticated Render API `/api/v1/internal/scan`. With server-side
   `ENABLE_VERIFIED_AUTO_IMPORT=true`, the API imports only the approved ATS
   sources, logs their production run, and then performs broad *read-only*
   discovery and stale-link maintenance. It uses the existing server database
   connection; no public write endpoint or browser credentials are needed.
2. **GitHub Actions** (`.github/workflows/ingest_jobs.yml`) is a separate
   read-only preflight, scheduled at 07:23 UTC. It runs tests and validates
   approved ATS links, but has no production database-writing path.

GitHub CI jobs were observed failing before steps started on 2026-11-08;
the GitHub runner is not the production scheduler. Vercel Hobby timing is
best-effort, and an accepted trigger is not proof that the background import
completed. Check Supabase run records.

## Explicit production approvals

Only registry entries with **both** `enabled: true` and
`auto_import_approved: true` are eligible for production writes. Current
bounded allowlist: GAMBIT Consulting (Personio), Nagarro (SmartRecruiters), Dow
(Workday), STATWORX (Personio), DYMATRIX (Personio), reeeliance (Personio),
Bosch Group (SmartRecruiters), and T-Systems ICT India (SmartRecruiters).
The latter four were added after read-only public-feed tests on 2026-11-09.
All other enabled ATS feeds remain read-only; in particular, the taod Personio
XML endpoint failed verification, so that source is disabled rather than
auto-imported.

The first unattended production run on 2026-11-09 (06:12 UTC) successfully
scanned all four originally approved feeds, saw 104 vacancies, accepted one,
updated one existing record, and inserted zero new rows, with no source errors.
The distinct discovery scan checked 801 listings from 11 sources and was
read-only. Adding feeds expands future coverage; it cannot guarantee more
eligible jobs under the unchanged profile constraints.

Every automatically imported job must additionally satisfy:

- A configured employer name, ATS type, exact approved posting hostname/path.
- Data Engineering positions in Germany **or India** with 0–2 years of
  advertised *minimum* required experience, or with experience not specified.
- SAP Basis positions in Germany **or India** with 0–5 years of advertised
  *minimum* required experience, or with experience not specified.
- Working Student, Internship, Junior, and Full-Time positions are eligible
  when they meet the role/country/experience rules. Unknown experience stays
  `NULL` and is shown as **Experience not specified**.
- German-language requirements are displayed; language is never an automatic
  exclusion. Technical/role scores support ranking, not an automatic 70% gate.
- A live, direct employer ATS application URL confirmed by bounded HEAD+GET;
  inaccessible, ambiguous, closed, or redirected-to-unapproved links are skipped.
- A maximum of five accepted jobs per approved board by default (board config
  can set `max_auto_import`, capped at 10) and 25 total per daily run.
- No sample fixture may be used for production writes. A job seen twice in the
  same run is not submitted twice.

The server-side importer applies the shared profile validation and performs
normalized company/title/location UPSERTs with stable job IDs and source provenance.
Closed links are only deactivated after two confirmed checks; rows and user
bookmarks are never deleted. The public jobs and sources endpoints expose only
records marked **live**, active, and not requiring review; newly approved
imports mark their exact verified employer URL live after the server UPSERT.
Uncertain or unchecked links never appear publicly. A repeated import of the
same official URL does not discard a previously confirmed `live` status.

The employer's prose takes priority when an entry-level label conflicts with
an explicit multi-year experience requirement. For example, the live GAMBIT
SAP Basis Consultant advertises *mehrjährige Erfahrung* despite Personio
`entry-level / 1-2` metadata; it is excluded from the junior/student profile.

## Authentication and credentials

The Vercel Cron route requires `CRON_SECRET`; Render's scheduled-scan endpoint
checks the corresponding `SCAN_TRIGGER_TOKEN`. Both belong exclusively in the
hosting providers' server-side configuration. They must never be exposed to
the browser or committed to the repository. The opt-in flag
`ENABLE_VERIFIED_AUTO_IMPORT=true` controls production imports on Render.

The standalone authenticated API importer still supports `INGEST_TOKEN`, but
the daily Vercel writer operates directly inside the trusted Render backend.
No GitHub Actions write secrets are required for the scheduled job.

## Verification and cutover checklist

1. Locally run `PYTHONPATH=backend python -m pytest -q backend/tests`.
2. Preview public sources without writes:
   `cd backend && python -m app.collectors.run_ingestion --approved-only --dry-run=true --verify-links`.
3. Verify Vercel's protected cron trigger forwards to Render successfully.
   The first approved import should log a separate `dry_run=false` row in
   `datapulse.ingestion_runs`; the broad scan logs `dry_run=true` separately.
4. Verify `inserted + updated = accepted` and inspect the exact resulting
   job records, source provenance, and website cards.
5. Check the next scheduled run and error logs. GitHub's independent read-only
   preflight can be repaired without affecting production imports.

### Rollback

Set `ENABLE_VERIFIED_AUTO_IMPORT=false` on Render. The Vercel Cron still performs
read-only scanning and stale-link maintenance, but no new jobs are written.
Do not delete imported jobs or saved applications. The GitHub workflow is
read-only regardless of this flag.

### Monitoring

Each Vercel-triggered execution stores a production import row and a distinct
read-only discovery row in Supabase with counts and failure reasons. The public
API exposes the latest completed run timestamp. The GitHub preflight prints a
JSON report and retains `datapulse-approved-ats-report` for 14 days if runners
are available. No external alert integration is configured yet.
