# Safe standalone deployment — Supabase / Render / Vercel

This checklist describes actions to be performed **after** the owner chooses the target Supabase organization and creates an accessible source repository. Do not treat uncompleted items as deployed.

## Phase 0: keep the existing Site alive

Do not delete or overwrite `https://data-pulse-germany-jobs.goldenball69.chatgpt.site`. Its managed D1 database is not the PostgreSQL database in this new project. The old site's 289 records and browser-local bookmarks are **not transferable by guessing an ID**. Obtain an authorized export first and preserve the export as an immutable backup. Stop before migration if favorite references cannot be mapped.

## Phase 1: create isolated PostgreSQL project

1. Choose an authorized Supabase organization and plan. Use a new named project such as `datapulse-standalone`, region Frankfurt (`eu-central-1`). **Confirm any quoted nonzero cost before creation**.
2. Open its SQL editor and apply `sql/schema.sql` to this new DB only. Verify `datapulse.jobs`, `datapulse.job_sources`, `datapulse.legacy_job_ids`, `datapulse.user_saved_jobs`, and `datapulse.ingestion_runs` exist, plus the `extensions.vector` column.
3. Run `sql/verification.sql`. A fresh DB should show zero jobs and zero bookmarks. Use `get_advisors`/Supabase advisor checks for exposure and permissions.
4. Obtain the official transaction-pooler connection string where the runtime lacks native IPv6. Store `DATABASE_URL` only in Render environment secrets and never in frontend code.

Supabase's Free plan has limited quotas and may pause after a week of low database activity. It is a development/hobby tier, not an SLA-backed production plan.

## Phase 2: push repository to GitHub

1. Create a new **private** GitHub repository, `datapulse-standalone`.
2. Extract the ZIP in the repo root and commit everything except `.env`, credentials, actual user data, databases, or caches.
3. Run unit tests and `npm install` / `npm run build` on your own machine (this runtime could not download npm dependencies). Commit the generated `frontend/package-lock.json` so future CI can use `npm ci`.
4. Set branch protection and review the initial deployment commit before enabling scheduled Actions.

## Phase 3: deploy FastAPI on Render

Use the included `render.yaml` Blueprint with the GitHub repo, or create a new Render Python Web Service (root directory `backend`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`). Set the following **private Render environment variables**:

| Variable | Example / purpose |
|---|---|
| `DATABASE_URL` | Dedicated Supabase PostgreSQL connection URL (secret) |
| `INGEST_TOKEN` | Random 32+ character secret, same in GitHub Actions |
| `FRONTEND_ORIGINS` | Exact Vercel HTTPS origin, comma-delimited for more |
| `SUPABASE_URL` | Optional; enables backend bookmark auth |
| `SUPABASE_PUBLISHABLE_KEY` | Optional; Supabase Auth only, public key |

Confirm `GET https://<render-service>.onrender.com/api/v1/health` returns `database_connected: true`. Render Free Web Services sleep after 15 minutes idle; first request on wake-up can be slow. It is a testing/hobby plan, not guaranteed always-on production.

## Phase 4: deploy Next.js on Vercel

Import the new GitHub repo into the **existing Vercel team**, choose Root Directory `frontend`, Framework `Next.js`, and configure:

`NEXT_PUBLIC_API_BASE_URL=https://<render-service>.onrender.com`

Build and deploy. Set backend `FRONTEND_ORIGINS` to the exact Vercel hostname. Verify `/api/v1/jobs` returns a JSON page and that filter controls and source badges work. Keep database secrets out of Vercel.

## Phase 5: verify real ingestion before enabling automation

1. On a machine or CI runner that can reach public ATS feeds, run `python -m app.collectors.run_ingestion --source greenhouse --board Superchat --dry-run` inside `backend` and inspect reported errors / accepted counts. A board may legitimately have zero qualifying jobs.
2. Verify a real, still-open Data Engineering job with >=3 technologies, English requirements, Germany eligibility and score >=0.70. Don't import an unverified role merely to increase a job counter.
3. Configure GitHub Actions secrets `DATAPULSE_API_BASE_URL` and `DATAPULSE_INGEST_TOKEN` (same value as Render `INGEST_TOKEN`), enable the workflow, then manually dispatch it once.
4. Verify `sql/verification.sql`: the expected job count, source names/types and zero duplicate source keys. Run an identical import twice: the second run should upsert, not add a duplicate. Verify the official employer URL remains preferred.
5. Only after success leave the daily GitHub schedule enabled. The `.github/workflows/ingest_jobs.yml` schedule runs at 05:17 UTC and may be delayed; GitHub may disable schedules in inactive public repos.

## Phase 6: move old jobs and saved records (optional, owner export required)

Use `scripts/import_legacy.py` against **real owner-provided JSON exports**. Dry run first. Old opaque IDs map to canonical PostgreSQL UUIDs via `datapulse.legacy_job_ids`. Distinct legacy rows with the same normalized identity can merge without dropping their ID mapping. Restoring server-side bookmarks requires a real authenticated Supabase user UUID; never fabricate or guess it. Browser-local saved jobs must be explicitly exported from the old Site and cannot be read cross-origin by the new Site.

## What remains unsupported or optional

- The v0.1 connectors have offline parser tests but no successful live public ATS fetch here; a production test is mandatory.
- No generalized browser crawler or universal SuccessFactors/Workday tenant discovery: each employer must have a verified, permitted feed.
- `embedding extensions.vector(1536)` is a column only; a true 1536-dimensional semantic match pipeline requires choosing and funding a matching embedding model/provider, generating vectors and checking dimensions before inserts.
- Frontend bookmarks are browser-local initially. To sync across devices, add a Supabase Auth UI that passes valid JWTs to the included authenticated bookmark endpoints.
- No automatic job applications, no account login bypass, no CAPTCHA bypass.
