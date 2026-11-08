# DataPulse — SAP Basis India/Germany registry expansion

Verification date: 2026-11-08. No production database writes authorized by this update.

## Discovery vs verified machine-readable feeds

- New public career feeds verified by a real HTTP 200 response: Personio for **GAMBIT Consulting** and **PlanOrg**; SmartRecruiters for **Nagarro**; Workday career-site JSON for **Accenture, Kyndryl, Hitachi, Deutsche Bank, Dow**. Some other verified Workday sites remain disabled until a broader candidate and capacity review.
- **Other employer portals are in `discovery_targets` only**: SAP SE / SAP Labs India, TCS, Infosys, Wipro, HCLTech, Tech Mahindra, Capgemini, Cognizant, Henkel, Siemens, BMW Group, BASF, Deutsche Telekom. Do not imply that these have active machine-readable API connectors until their exact endpoints are verified.
- **Vesterling and DEVjobs DE** are third-party recruiters/boards; they are not direct employer ATS feeds. They are not silently merged into `direct_ats` ingestion. `workidentity` has a public Personio feed but advertises client positions, so is also kept discovery-only.
- SAP SuccessFactors has tenant-specific syndicated XML feeds, **not a universal public feed**. No SuccessFactors feed credentials or endpoints have been fabricated.
- Workday endpoints here are the **public careers site's job-search JSON requests**, not Workday's documented customer enterprise API. Honor applicable terms and `robots.txt`, never bypass restrictions, and cap request volume.

## Three profile rules

- `sap_basis_5y` → `sap_basis_experienced`: countries `DE`/`IN`; explicit minimum years 3–6; max years unknown or up to 8. A title such as “Senior SAP Basis Consultant” is eligible with a credible five-year requirement, unlike director/principal titles.
- `sap_basis_student` → `sap_basis_student`: country `DE` only; explicit working student/internship/trainee/junior title; experience requirement is waived; canonical minimum becomes 0 with explanatory evidence.
- `data_eng_junior` → `data_engineering_junior`: country `DE` only; no required experience >2; explicit junior/student title required if no numeric requirement; strong technical fit (0.70+) and verified English-friendly requirements still enforced.

An unspecified employer experience requirement is kept `NULL`; no invented experience or location. German “3 bis 6 Jahre” and English “4-6 years”, “5+ years”, “5 yrs” are handled conservatively. Functional SAP FI/CO/SD roles are not SAP Basis.

## Reproducible local tests

```
cd backend
PYTHONPATH=. python -m pytest -q
python -m app.collectors.run_ingestion --expand-registry \
  --target-profiles sap_basis_5y,sap_basis_student,data_eng_junior --dry-run=true
```

The SQLite in-memory insert/update simulation proves three persona rows, stable record IDs under upsert, and unchanged bookmark references; it is **not a production PostgreSQL transaction test**.

The default Vercel cron job invokes the first 11 legacy boards only. Before upgrading the cron's board count, validate the expanded scan's per-source latency, robots compliance, error rates, and full ATS posting descriptions.

References: https://developers.smartrecruiters.com/docs/endpoints ; https://learning.sap.com/courses/sap-successfactors-recruiting-candidate-experience-administration/posting-jobs
