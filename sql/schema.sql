-- DataPulse standalone. Fresh independent database ONLY. Does not modify the original Site.
-- Run once in a new Supabase/Neon database, not against an existing unrelated project.
BEGIN;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
CREATE SCHEMA IF NOT EXISTS datapulse;
DO $$ BEGIN
  CREATE TYPE datapulse.source_type_enum AS ENUM ('direct_ats','company_portal','job_board','aggregator');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE TYPE datapulse.work_mode_enum AS ENUM ('On-site','Hybrid','Remote','Unknown');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE TYPE datapulse.level_enum AS ENUM ('Working Student','Internship','Junior','Mid','Senior','Lead','PhD / Postdoc');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS datapulse.jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_title text NOT NULL,
  company_name text NOT NULL,
  location text NOT NULL,
  region text,
  country_code varchar(2),
  country varchar(50),
  min_experience_years integer,
  max_experience_years integer,
  experience_evidence text,
  persona_tags text[] NOT NULL DEFAULT '{}'::text[],
  normalized_company_name text NOT NULL,
  normalized_job_title text NOT NULL,
  normalized_location text NOT NULL,
  work_mode datapulse.work_mode_enum NOT NULL DEFAULT 'Unknown',
  employment_type text NOT NULL DEFAULT 'Unknown',
  level datapulse.level_enum NOT NULL DEFAULT 'Mid',
  domain_category text NOT NULL DEFAULT 'Data Engineering',
  language_required text NOT NULL DEFAULT 'Unknown',
  is_english_friendly boolean NOT NULL DEFAULT false,
  visa_supported boolean NOT NULL DEFAULT false,
  relocation_assistance boolean NOT NULL DEFAULT false,
  salary_range varchar(100),
  salary_currency varchar(3),
  salary_evidence text,
  last_link_checked_at timestamptz,
  link_status varchar(24) NOT NULL DEFAULT 'unchecked',
  link_failures integer NOT NULL DEFAULT 0,
  link_detail varchar(160),
  link_last_confirmed_at timestamptz,
  description text,
  tech_stack text[] NOT NULL DEFAULT '{}'::text[],
  match_score double precision NOT NULL DEFAULT 0 CHECK (match_score BETWEEN 0 AND 1),
  embedding extensions.vector(1536), -- optional semantic embedding, NOT auto-populated
  official_apply_url text NOT NULL,
  source_name text NOT NULL DEFAULT 'DataPulse',
  source_type datapulse.source_type_enum NOT NULL DEFAULT 'job_board',
  source_key text,
  date_posted date,
  date_evidence text,
  is_active boolean NOT NULL DEFAULT true,
  needs_review boolean NOT NULL DEFAULT false,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  last_verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_datapulse_experience_bounds CHECK (
    (min_experience_years IS NULL OR min_experience_years BETWEEN 0 AND 40) AND
    (max_experience_years IS NULL OR max_experience_years BETWEEN 0 AND 40) AND
    (min_experience_years IS NULL OR max_experience_years IS NULL OR min_experience_years<=max_experience_years)
  ),
  CONSTRAINT uq_datapulse_job_identity UNIQUE (normalized_company_name,normalized_job_title,normalized_location)
);
-- Preserve all sightings, including an aggregator URL replaced as primary by the ATS URL.
CREATE TABLE IF NOT EXISTS datapulse.job_sources (
  id bigserial PRIMARY KEY,
  job_id uuid NOT NULL REFERENCES datapulse.jobs(id) ON DELETE RESTRICT,
  source_key text NOT NULL UNIQUE,
  source_name text NOT NULL,
  source_type datapulse.source_type_enum NOT NULL,
  apply_url text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz,
  UNIQUE (job_id, source_name, apply_url)
);
-- Auth is verified by FastAPI, never trust a user_id supplied by browser requests.
CREATE TABLE IF NOT EXISTS datapulse.legacy_job_ids (
  legacy_id text PRIMARY KEY,
  job_id uuid NOT NULL REFERENCES datapulse.jobs(id) ON DELETE RESTRICT,
  migrated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS datapulse.user_saved_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  job_id uuid NOT NULL REFERENCES datapulse.jobs(id) ON DELETE RESTRICT,
  saved_at timestamptz NOT NULL DEFAULT now(),
  application_status varchar(32) NOT NULL DEFAULT 'Saved',
  status_updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id,job_id)
);
CREATE TABLE IF NOT EXISTS datapulse.ingestion_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  dry_run boolean NOT NULL DEFAULT false,
  sources_attempted integer NOT NULL DEFAULT 0,
  sources_succeeded integer NOT NULL DEFAULT 0,
  jobs_seen integer NOT NULL DEFAULT 0,
  accepted integer NOT NULL DEFAULT 0,
  inserted integer NOT NULL DEFAULT 0,
  updated integer NOT NULL DEFAULT 0,
  rejected jsonb NOT NULL DEFAULT '{}'::jsonb,
  failures jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_jobs_country_experience ON datapulse.jobs (country_code,min_experience_years,max_experience_years);
CREATE INDEX IF NOT EXISTS idx_jobs_persona_tags ON datapulse.jobs USING GIN (persona_tags);
CREATE INDEX IF NOT EXISTS idx_jobs_country_domain ON datapulse.jobs (country_code,domain_category);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON datapulse.jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_posted ON datapulse.jobs (date_posted DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_jobs_region ON datapulse.jobs (region);
CREATE INDEX IF NOT EXISTS idx_jobs_level ON datapulse.jobs (level);
CREATE INDEX IF NOT EXISTS idx_jobs_mode ON datapulse.jobs (work_mode);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON datapulse.jobs (match_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_skills ON datapulse.jobs USING GIN (tech_stack);
CREATE INDEX IF NOT EXISTS idx_jobs_source_key ON datapulse.jobs (source_key);
CREATE INDEX IF NOT EXISTS idx_jobs_active_score ON datapulse.jobs (is_active,match_score DESC);

-- In Supabase the datapulse schema is not exposed to the Data API by default.
-- Direct PostgreSQL credentials are backend-only. Revoke access from public API roles.
REVOKE ALL ON SCHEMA datapulse FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
    REVOKE ALL ON SCHEMA datapulse FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
    REVOKE ALL ON SCHEMA datapulse FROM authenticated;
  END IF;
END $$;
ALTER TABLE datapulse.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE datapulse.job_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE datapulse.legacy_job_ids ENABLE ROW LEVEL SECURITY;
ALTER TABLE datapulse.user_saved_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE datapulse.ingestion_runs ENABLE ROW LEVEL SECURITY;
-- No public RLS policies. API connects with a private DB owner credential only.
COMMIT;
