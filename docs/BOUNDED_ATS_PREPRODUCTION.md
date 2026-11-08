# DataPulse bounded ATS verification — pre-production

**Run observed:** 2026-11-08. **Production writes:** disabled.

## Reproduce

From `backend/`:

```bash
python -m app.collectors.run_ingestion \
  --sources sap_partners,databricks_partners,softgarden,successfactors \
  --target-profiles sap_basis_5y,sap_basis_student,data_eng_junior \
  --dry-run=true \
  --output-report=./reports/partner_engine_verification.json
```

This is a **read-only partner-only mode**, not an authentication bypass or
a production ingestion trigger. The CLI exits rather than accepting
`--apply` or `--dry-run=false` in this mode. The file is written locally
to `backend/reports/partner_engine_verification.json`.

## Runtime safeguards

- Exactly one shared **`asyncio.Semaphore(10)`** limits outbound
  HTTP requests. Each ATS host is further limited to two simultaneous requests.
- All network clients have **5-second HTTP request timeouts**.
- An employer has a bounded execution budget (35 seconds), and
  Workday/SmartRecruiters detail downloads are capped to three per employer
  in this pre-production workflow.
- Robots exclusions, access requirements (401/403), 429 throttling and
  redirects are recorded; no evasion or retry is attempted.
- An employer ATS name or slug is only accepted from an observed official
  career-page link or an already verified existing board. Company domains are
  **not** blindly substituted into ATS endpoint templates.
- Softgarden's public JSON path is probed **only on a directly observed**
  Softgarden host. SuccessFactors OData endpoints frequently require
  authentication and are never accessed with guessed tokens/credentials.
- schema.org `JobPosting` JSON-LD is parsed on actual employer pages;
  expired, country-unverified and mismatched-organization postings are skipped.
- No call to Supabase, PostgreSQL or the authenticated ingestion API exists
  in the bounded verification pipeline.

## Strict pre-production rules

The **SAP Basis Experienced** filter accepts Germany/India administrative
titles with explicit 3–6 years required. A `5+` minimum with no maximum
is flagged as unverified rather than assumed to cap at six years.

**SAP Basis Student/Entry** requires German location and an explicit
Werkstudent, internship or junior/entry role. Senior executive roles and
non-German vacancies are excluded. Experience limits are bypassed for these
role types.

**Data Engineering Junior/Entry** requires Germany, maximum two years
of explicit experience or a bona fide student placement without prior
experience, plus the existing technical, English and match-quality rules.
Any 3+ experience requirement is excluded.

These stricter rules are confined to this pre-production directory report;
the user-facing production API profile contract is unchanged.

## Actual observed run

- 16 employer pages in scope; directory memberships and partner tiers
  **not independently verified** by dynamic public directory pages
- 2 public ATS feeds validated: STATWORX (Personio, 23 job records)
  and Accenture (Workday, 3 job records)
- 26 postings inspected; 0 accepted across the three strict profiles
- 22 unrelated titles, 1 DE experience above 2 years, 3 unverified locations
- One SuccessFactors candidate URL from a careers page; no public validated
  SuccessFactors vacancy feed. No observed public Softgarden feed.
- 0 inserted; 0 updated. No connector exceptions in the final report.

The attached report `docs/reports/partner_engine_verification_2026-11-08.json`
is an **audited summary reconstructed from observed scan logs**.
The original full JSON was written successfully to the temporary networked
environment, but was not retained after its session ended.

References: SAP SuccessFactors API authentication documentation and Google
Search Central's JobPosting structured-data specification.
