SELECT count(*) AS jobs_total, count(*) FILTER (WHERE is_active) AS active FROM datapulse.jobs;
SELECT source_name,source_type,count(*) AS jobs FROM datapulse.jobs GROUP BY source_name,source_type ORDER BY jobs DESC;
SELECT count(*) AS saved_bookmarks FROM datapulse.user_saved_jobs;
SELECT count(*) AS repeated_source_keys FROM (SELECT source_key FROM datapulse.job_sources GROUP BY source_key HAVING count(*)>1) x;
SELECT count(*) AS ambiguous_job_keys FROM (SELECT normalized_company_name,normalized_job_title,normalized_location FROM datapulse.jobs GROUP BY 1,2,3 HAVING count(*)>1) x;
