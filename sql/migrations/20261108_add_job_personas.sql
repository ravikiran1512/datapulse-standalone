-- Non-destructive migration: DataPulse standalone only.
-- Experience unknown is NULL, never silently changed to 0 or 10.
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE datapulse.jobs ADD COLUMN IF NOT EXISTS country_code varchar(2);
ALTER TABLE datapulse.jobs ADD COLUMN IF NOT EXISTS country varchar(50);
ALTER TABLE datapulse.jobs ADD COLUMN IF NOT EXISTS min_experience_years integer;
ALTER TABLE datapulse.jobs ADD COLUMN IF NOT EXISTS max_experience_years integer;
ALTER TABLE datapulse.jobs ADD COLUMN IF NOT EXISTS experience_evidence text;
ALTER TABLE datapulse.jobs ADD COLUMN IF NOT EXISTS persona_tags text[] NOT NULL DEFAULT '{}'::text[];

-- Backfill country ONLY when already grounded by a Germany-qualified location.
UPDATE datapulse.jobs
   SET country_code='DE', country='Germany'
 WHERE country_code IS NULL AND
       (location ILIKE '%Germany%' OR location ILIKE '%Deutschland%');

CREATE INDEX IF NOT EXISTS idx_jobs_country_experience
 ON datapulse.jobs (country_code,min_experience_years,max_experience_years);
CREATE INDEX IF NOT EXISTS idx_jobs_persona_tags
 ON datapulse.jobs USING GIN (persona_tags);
CREATE INDEX IF NOT EXISTS idx_jobs_country_domain
 ON datapulse.jobs (country_code,domain_category);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_datapulse_experience_bounds'
                AND conrelid='datapulse.jobs'::regclass) THEN
  ALTER TABLE datapulse.jobs ADD CONSTRAINT ck_datapulse_experience_bounds CHECK (
    (min_experience_years IS NULL OR min_experience_years BETWEEN 0 AND 40) AND
    (max_experience_years IS NULL OR max_experience_years BETWEEN 0 AND 40) AND
    (min_experience_years IS NULL OR max_experience_years IS NULL OR min_experience_years<=max_experience_years)
  );
 END IF;
END $$;
COMMIT;
