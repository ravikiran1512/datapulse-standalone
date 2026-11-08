# SAP & Databricks Partner Directory Resolver

## Purpose

This is a **read-only, bounded candidate-discovery** pipeline. It uses official
SAP Partner Finder and Databricks Partner Directory pages as leads, then inspects
employer-owned career pages for genuine public ATS links. Directory membership
or Gold/Platinum/Premier/Select tier is **never inferred** from an employer's name.

Official references:
- SAP: https://partnerfinder.sap.com/partnerNavigator
- Databricks: https://www.databricks.com/partners/partner-directory

Some directory results require JavaScript, pagination, or permission. Those
companies remain `not_visible_or_dynamic` or `robots_unverified_or_disallowed`
rather than being asserted as non-partners. No authorization, CAPTCHA, rate-limit
or robots restriction is bypassed.

## Run the partner-only dry-run

From the `backend/` directory:

```bash
python -m app.collectors.run_ingestion \
  --source partner_directories \
  --ecosystems sap,databricks \
  --profiles sap_basis_5y,sap_basis_student,data_eng_junior \
  --dry-run=true
```

By default, 6 targets are sampled in round-robin order across ecosystems, and
up to 3 verified public ATS feeds are queried. For a slower but wider manual
check, add `--max-partners 16 --max-feeds 5`. Never treat the configured
target list as a complete or independently certified partner directory.

The module refuses `--dry-run=false` and `--apply`. It does not import
the database library, open a PostgreSQL connection, or submit authenticated
ingestion requests. Results are printed as JSON. They include directory
visibility, ATS candidate links, feed validation, per-profile counts, explicit
rejection reasons, and up to 8 official job URLs.

## Strict partner-only eligibility rules

- **SAP Basis experienced:** Germany or India; SAP Basis administration role;
  explicit **minimum 4 years** and **maximum 7 years**; no student contract,
  executive, principal, or unrelated SAP functional module.
  Open-ended requirements such as `5+ years` without a documented maximum
  are flagged for review, not assumed to cap at 7.
- **SAP Basis student:** Germany only; title or contract explicitly says
  Werkstudent, Working Student, internship, Praktikum, or Studentische
  Aushilfe. Ordinary permanent entry-level jobs are **not** student jobs.
- **Data Engineering junior:** Germany only; engineering title and explicit
  requirement <=2 years (or a true student/intern contract with no years
  requirement). Any 3+ requirement or range ending beyond 2 is excluded.
  Existing English/skills/fit safeguards remain in place.

These rules are specific to this partner-discovery report. They do not
silently change the existing user-facing profile search or the older employer
scanner. Align the global profiles only in a separately tested application
change.

## ATS discovery rules

Never derive a guessed ATS slug from a brand name or employer website domain.
A candidate needs either a real ATS link seen on the employer's public career
page or an enabled, previously verified matching employer board. Only supported
public feed types are queried (Personio XML, Greenhouse, Lever, Ashby,
SmartRecruiters and Workday). SuccessFactors and Softgarden links require
site-specific structured feed verification and are discovery-only by default.

`config/partner_directory_targets.json` has 16 leads, not 16 proven memberships.
A directory name merely visible in publicly loaded HTML is not evidence of
a specific partner tier or region-specific certification. Confirm from a
specific official partner profile before assigning any tier.

## Operational safety

- Read-only (0 inserts and 0 updates).
- HTTPS only; robots.txt access checks; no automatic redirect following.
- Per-request timeouts, HTML size limits, small board caps.
- No automatic promotion to production, no scheduled job writes.
- Employer source failures or directory JS limitations are reported, not hidden.
- Run tests: `PYTHONPATH=backend python -m pytest backend/tests/test_partner_directories.py`
