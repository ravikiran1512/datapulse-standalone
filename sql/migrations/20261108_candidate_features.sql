-- Additive migration. Existing jobs, bookmarked UUIDs and ingestion history preserved.
BEGIN;
ALTER TABLE datapulse.jobs
  ADD COLUMN IF NOT EXISTS visa_supported boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS relocation_assistance boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS salary_range varchar(100),
  ADD COLUMN IF NOT EXISTS salary_currency varchar(3),
  ADD COLUMN IF NOT EXISTS salary_evidence text,
  ADD COLUMN IF NOT EXISTS last_link_checked_at timestamptz,
  ADD COLUMN IF NOT EXISTS link_status varchar(24) NOT NULL DEFAULT 'unchecked',
  ADD COLUMN IF NOT EXISTS link_failures integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS link_detail varchar(160),
  ADD COLUMN IF NOT EXISTS link_last_confirmed_at timestamptz;
ALTER TABLE datapulse.user_saved_jobs
  ADD COLUMN IF NOT EXISTS application_status varchar(32) NOT NULL DEFAULT 'Saved',
  ADD COLUMN IF NOT EXISTS status_updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_datapulse_job_link_review
  ON datapulse.jobs (is_active,last_link_checked_at ASC NULLS FIRST)
  WHERE needs_review = false;
CREATE INDEX IF NOT EXISTS idx_datapulse_saved_status
  ON datapulse.user_saved_jobs (user_id,application_status,saved_at DESC);
COMMIT;
