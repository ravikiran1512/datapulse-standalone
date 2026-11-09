# DataPulse Standalone — Germany Data Engineering Jobs

A complete **independent application codebase** for migrating DataPulse away from its existing ChatGPT Sites deployment. The old private Site and its data are **untouched**.

- **Frontend:** Next.js 16 / React 19 / Tailwind CSS, dark DataPulse UI with accessible custom dropdowns.
- **Backend:** FastAPI / asyncpg, parameterized Boolean query search, pagination, source badges, account-verified bookmark API, authenticated ingestion.
- **Database:** isolated PostgreSQL schema `datapulse` with pgvector (`embedding vector(1536)`), stable UUIDs, source appearance history, normalized deduplication and bookmark table.
- **Ingestion:** public Greenhouse, Lever, Ashby, Personio, Workday, SuccessFactors connectors from the previously tested ATS package; daily GitHub Actions workflow (disabled until you add GitHub secrets and publish repo).
- **Deployment:** Render `render.yaml`, Vercel `frontend/`, optional backend Dockerfile.

> **Status:** 2026-11-08: local source implementation and automated Python tests. No new Supabase project, Vercel site, Render service, GitHub repo, live ATS ingestion or data migration has been created. `npm install` timed out in this sandbox and a real Next.js build could not be verified; TSX parser checks passed. The provided initial source registry is **not** a claim that all boards are live or currently contain qualifying jobs.

## Repository layout

```text
frontend/                   Next.js app (copy of the familiar DataPulse UX)
backend/app/main.py         Search API, authenticated ingestion, saved-job endpoints
backend/app/db.py           Async PostgreSQL upsert and official URL precedence
backend/app/search.py       Safe quoted Boolean parser (AND / OR / NOT)
backend/app/collectors/     Public ATS schedule runner
backend/datapulse_ingestion/ Reused modular ATS parsers and hard matching policy
backend/tests/             Backend API, role, URL, deduplication and search tests
sql/schema.sql             PostgreSQL / Supabase pgvector schema
sql/verification.sql       Safe validation queries
config/verified-boards.json Named public employer board registry
scripts/import_legacy.py   Audited import from a real owner-provided Site export
.github/workflows/         Daily scheduled ingestion job
render.yaml                Render Blueprint
```

## Local development

### 1. Start a dedicated PostgreSQL database

Use an **independent** Supabase or Neon PostgreSQL project, not your existing QueryQuest database. Run `sql/schema.sql` in the SQL editor or via `psql`. The script creates a private `datapulse` schema and enables `pgvector`. Never expose `DATABASE_URL` to the browser. Supabase projects created after October 2026 do not auto-expose new SQL tables to the REST Data API. This app accesses PostgreSQL from the backend instead.

### 2. Start FastAPI

```bash
cd backend
python -m venv .venv
source .venv/bin/activate # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env # put real DATABASE_URL and INGEST_TOKEN here, NOT in Git
set -a; source .env; set +a
uvicorn app.main:app --reload --port 8000
```

Verify `http://localhost:8000/api/v1/health` reports `database_connected: true` after provisioning the database. API docs: `/docs`.

### 3. Start Next.js

```bash
cd frontend
npm install
cp .env.example .env.local
# NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
```

Visit `http://localhost:3000`. Search supports quoted phrases and Boolean `AND`, `OR`, `NOT`, with parentheses. Filters, paging, and source labels are backed by FastAPI. Saved jobs persist in browser local storage until Supabase Auth is wired to your frontend; the backend already has authenticated bookmark endpoints for future account sync.

### 4. Dry-run a configured Greenhouse board

```bash
cd backend
python -m app.collectors.run_ingestion --source greenhouse --board Superchat --dry-run
# Completely offline synthetic test (NOT a live vacancy):
python -m app.collectors.run_ingestion --source greenhouse --board Superchat \
  --dry-run --fixture tests/fixtures/greenhouse-synthetic.json
```

For a real production collector, configure `API_BASE_URL` and `INGEST_TOKEN` as GitHub **Action secrets**, then run `python -m app.collectors.run_ingestion --target api` through the workflow. Ingest writes require a 32+ character bearer token and pass all filtering rules server-side.

### 5. Run tests

```bash
cd backend
pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q tests
```

## Important behavior

- Ingestion excludes senior/lead/principal/PhD/director roles and non-technical BI-only positions; student, internship, junior and mid roles are eligible.
- Germany eligibility is verified using ATS location/country signals. International remote positions are not assumed Germany-eligible.
- At least 3 distinct engineering technologies, 2 core skills, explicit English language evidence and a technical cosine score >= 0.70 are required at write time.
- The **technical skill cosine** is computed on a deterministic weighted technology feature vector, not the 1536-dimensional optional `embedding` column. The embedding column remains NULL until a *separate embedding provider* is configured. Do not call the technical match score a hiring probability.
- A canonical `(normalized_company_name,normalized_job_title,normalized_location)` unique key deduplicates sources while retaining all original URLs in `job_sources`. **Two genuinely different requisitions with identical company/title/city could be merged**; extend the key with an ATS requisition number if you encounter this case.
- A direct-ATS/company-portal URL replaces an aggregator as the preferred application link; subsequent aggregator scans cannot overwrite the preferred official URL.
- The job record keeps a stable PostgreSQL UUID. Existing bookmarks stay attached when the canonical job is updated.
- ATS dates and work modes not present in the underlying feed remain unknown, not invented.
- Failed source scans are recorded as failures, not interpreted as no vacancies. Jobs are not automatically marked closed merely for being missing from a scan.

## Going live

Read **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** before deploying. The source ZIP is an installable starting point, not a live replacement site. A complete cutover requires a real export of the ChatGPT Site jobs and saved favorites; the previous Site is managed in a different hosting environment.

**Security:** keep secret credentials out of Git and `NEXT_PUBLIC_` variables. Restrict CORS to the actual Vercel origin. If using Supabase Auth, enable and configure your users in Supabase; the backend confirms bearer identity with Supabase Auth instead of trusting a supplied `user_id`.


## Multi-profile search (2026-11-08)

DataPulse now supports multiple simultaneous job profiles via repeated query parameters:

```text
GET /api/v1/jobs?persona=sap_basis_experienced&persona=sap_basis_student&country=DE
GET /api/v1/jobs?persona=data_engineering_junior&country=DE
GET /api/v1/profiles
```

Presets: **SAP Basis** (Germany/India, employer's published minimum required experience at most 5 years or not specified) and **Data Engineering** (Germany/India, minimum required experience at most 2 years or not specified). The database retains three legacy `persona_tags` for backward compatibility, while the website groups the two SAP tags into one SAP Basis track. Working Student, Internship, Junior, and Full-Time positions are included. A 70% technical match is **never** required for ingestion; ranking still uses the score. German-required jobs are accepted and the language is shown on their cards. Search filters remain optional; public jobs require a verified **live** official ATS URL.

Ingested job records include `country_code` (`DE` / `IN`), nullable `min_experience_years` and `max_experience_years` (unknown is **not zero**), `experience_evidence`, and `persona_tags`. Do not treat `SAP Basis` relevance scores as resume cosine scores: SAP uses a role-based relevance heuristic while Data Engineering uses the existing technical skill cosine score. Browser bookmarks remain intact. The scan endpoint remains strictly read-only; production writes require separate authenticated ingestion.

**Existing database upgrade:** Apply `sql/migrations/20261108_add_job_personas.sql` to the dedicated DataPulse Supabase database. It is idempotent and does not delete data or bookmarks. The full `sql/schema.sql` includes the same columns for brand-new installations.

**Coverage:** The current configured employer feeds are not a guarantee of nationwide India SAP Basis coverage. Add verified employer board identifiers (especially Indian employers) separately; no candidate is displayed as real without a legitimate ATS posting. Experience parsing is conservative and may require manual review when language varies.

## SAP Basis registry expansion (India + Germany, November 2026)

The repository now supports the three profile presets for a verified subset of public
career feeds: SAP Basis (Germany and India, at most five required years or unknown)
and Data Engineering (Germany and India, at most two required years or unknown).
Existing API persona aliases remain valid. Unknown years are displayed as
"Experience not specified" and never fabricated as zero.

### Read-only live scan

```bash
cd backend
python -m app.collectors.run_ingestion --expand-registry \
  --target-profiles sap_basis_5y,sap_basis_student,data_eng_junior --dry-run=true
```

The default and requested command **never inserts records**. The runner requires
both `--dry-run=false` and `--apply`, plus API credentials, to write jobs. The
existing daily read-only scan still checks the 11 legacy boards until a separate
load/reliability review.

The list of verified ATS feeds and separate discovery-only employer career portals
lives in [`config/verified-boards.json`](config/verified-boards.json). A public
employer homepage is **not** an active ATS feed. SAP SuccessFactors XML feeds are
employer-specific and must be provided/approved; no generic endpoint is assumed.
Workday job search uses a career site's JSON requests (not a documented Workday
public job board API), with public-site robots and polite rate limits.

The synthetic/SQLite integration tests do **not** prove live vacancies or actual
PostgreSQL migration results. See [`docs/SAP_BASIS_REGISTRY_EXPANSION.md`](docs/SAP_BASIS_REGISTRY_EXPANSION.md)
for source status, local checks, and limitations.

### November 2026 candidate feature upgrade

The default frontend preset selection is empty ("Show All"). Candidates can now save application statuses locally, export saved jobs as escaped CSV, inspect a profile-based skill-gap drawer, and see evidence-backed language/visa/salary indicators. The API provides corresponding metadata, authenticated bookmark-status updates and bounded daily ATS stale-link checks. The partner directory resolver is in `backend/app/collectors/discover_partners.py` and intentionally reports unverified feed leads rather than auto-ingesting them. Details: `docs/CANDIDATE_FEATURES_AND_PARTNER_DISCOVERY.md`. Apply `sql/migrations/20261108_candidate_features.sql` before deployment.

STATWORX Personio XML was verified (HTTP 200, 23 live positions); the registry now has 20 enabled ATS sources, with 11 checked per daily read-only scan on a rotating basis. Partner directory discovery also runs weekly, logging candidates without writing jobs.
