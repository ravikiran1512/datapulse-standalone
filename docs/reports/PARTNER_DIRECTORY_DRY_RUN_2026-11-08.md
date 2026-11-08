# DataPulse partner directory dry-run — 2026-11-08

This is an **observed, read-only two-employer live sample**, not a complete
16-target partner census. The initial six-target run exceeded the temporary
test environment lifetime and has **no verified summary**.

## Observed live results

- Source: `partner_directories`
- Ecosystems: SAP and Databricks
- Targets checked: **2** (NTT DATA Business Solutions, STATWORX)
- Public directory names visibly confirmed: **0**
- ATS feeds validated: **1** (STATWORX Personio)
- Live employer jobs examined: **23**
- Eligible SAP Basis experienced (4–7 years): **0**
- Eligible SAP Basis student / Werkstudent (Germany): **0**
- Eligible Germany junior Data Engineering (≤2 years): **0**
- Rejected: **22 unrelated titles**, **1 Data Engineering role with ≥3 years**
- Database inserts: **0**; updates: **0**

## Source evidence and limitations

- NTT DATA Business Solutions: employer career page fetched successfully; SAP
  Partner Finder lookup was **robots_unverified_or_disallowed**. No usable
  publicly verified ATS feed was found in this sample.
- STATWORX: public Personio feed verified, **23 postings** returned; the
  Databricks directory's loaded public HTML did **not** independently confirm
  the partner name. Do **not** infer current partner tier or certification.
- Some partner-directory content is JavaScript-rendered. An absent name in
  a loaded page is not proof of non-partnership.
- Softgarden, SAP SuccessFactors and employer-linked RSS/JSON feeds are
  discovery candidates only until their public vacancy schemas are validated.
- All matches remain subject to official job-link and profile verification
  before separate production ingestion is authorized.

## Reproduction

```bash
cd backend
python -m app.collectors.run_ingestion \
  --source partner_directories \
  --ecosystems sap,databricks \
  --profiles sap_basis_5y,sap_basis_student,data_eng_junior \
  --dry-run=true
```

The default command samples six interleaved partner targets. Smaller bounded
samples are useful when ATS or directory pages are slow; the pipeline is
strictly read-only regardless of flags.
