# Architecture and data flow

```
Verified public Greenhouse / Lever / Ashby / Personio / Workday / SuccessFactors boards
 | read-only rate-limited public API requests
 v
Python connector -> normalize policy -> English/skills/seniority/location/score filter
 | <=25 jobs per authenticated batch
 v
FastAPI POST /api/v1/internal/ingest [INGEST_TOKEN, server only]
 | repeated write-boundary validation
 v
asyncpg transaction -> datapulse.jobs + datapulse.job_sources + ingestion_runs
 | normalized identity UPSERT, stable UUID, direct link precedence
 v
FastAPI GET /api/v1/jobs <---- Next.js (Vercel)
 | local bookmarks in browser
 v
public pagination, filtering, source badges, apply links

Optional Supabase Auth -> API token verification -> datapulse.user_saved_jobs
```

No direct browser/PostgreSQL connection. `datapulse` is a private Postgres schema, and tables have no public grants or RLS policies. Backend access is through the server's private DATABASE_URL. The official application link is always an employer URL from the ATS feed, never a generated application destination.

## Why not blindly use the exact identity unique index?

Company/title/location may identify more than one requisition (two separately posted Junior Data Engineer jobs in Berlin). This MVP uses a uniqueness policy for cross-board de-duplication and records every source sighting. At enterprise scale add verified ATS job requisition IDs (and possibly distinguishing team/department) to avoid false merges. That improvement should precede aggressive catalog expansion.

## Query safety

Boolean search is compiled to SQL with positional `$n` bind parameters. Unknown fields cannot become SQL identifiers. Only allowlisted sort orders and enumerated filter values are permitted. Writes require a separate long bearer token. No public signup means no cross-user bookmark access.

## Availability semantics

`is_active=true` means last accepted by the crawler, not that the employer guarantees it is still accepting applications. Missing from one feed is insufficient closure evidence. Persist source-level successful scans and require explicit closure confirmation before removing a previously verified posting.
