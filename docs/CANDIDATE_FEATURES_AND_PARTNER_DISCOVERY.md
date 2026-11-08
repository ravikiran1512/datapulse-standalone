# DataPulse candidate features and partner discovery (November 2026)

## Default profile state
No checkboxes are selected on initial load; the backend interprets the absence of `persona` query params as **all three presets**, each with independent country/experience limits. Users may clear every checkbox and still receive all eligible jobs. Explicit profile selections narrow the result set with OR logic.

## Candidate features
- Fit analysis is a **preset skill-gap explanation**, NOT a resume upload or predictive hire score. It uses job `tech_stack`, static profile targets, and known experience brackets; no personal resume has been connected.
- Saved jobs and the status workflow (`Saved`, `Applied`, `Interviewing`, `Archived`) are persisted in this browser with the existing saved-job keys plus a new status key. **They do not sync across devices without user sign-in.** The authenticated FastAPI PATCH endpoint is prepared for the later Supabase Auth integration.
- Export Saved Jobs creates a CSV in the browser, includes status and official apply URL, and escapes cells that could be spreadsheet formulas. No personal data is sent to a third-party export service.
- Language: not requiring C1 German does **not** establish English-friendly. Positive English evidence is required. Visa: relocation assistance without explicit sponsorship is not classified as sponsorship. Salary: only numeric, plausibly bounded, explicitly declared employer ranges are displayed.
- The stale-link maintenance runs as part of the same authenticated daily Vercel Cron / Render scan; it does not add a second cron. It is rate-limited to 25 rows per run, requests only known public ATS hosts, confirms `HEAD` ambiguity using `GET`, and requires two confirmed closed responses before deactivation. Unsupported hosts and timeouts are kept active for review.

## Partner ATS resolver

`python -m app.collectors.discover_partners --max-targets 8 --output /tmp/partner-leads.json`

The `config/partner_targets.json` file stores official career pages for selected partner-network candidates, not asserted Gold/Premier partner certifications. The script:
1. Attempts each page's robots.txt before retrieving it.
2. Extracts ATS links from the employer's own page; never guesses a Personio/Greenhouse slug.
3. Classifies Personio, Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Softgarden and SuccessFactors URLs as candidate links.
4. Produces a review report. It **does not automatically promote a candidate to an active feed** or write any jobs.

Be aware that directories relying on JavaScript or authenticated access may not expose listings through this approach; those are left unresolved. SuccessFactors and Softgarden require individually verified public schemas, not assumed endpoints.

## Safety and rollout
1. Apply `sql/migrations/20261108_candidate_features.sql` to the existing isolated Supabase DataPulse project. The migration is additive, preserving IDs, all bookmarks and source history.
2. Deploy the updated FastAPI backend, verify `/api/v1/health`, `/api/v1/profiles`, `/api/v1/jobs`, and scan endpoint authentication.
3. Deploy the frontend directly to the existing Vercel Hobby project. Keep `CRON_SECRET` and `SCAN_TRIGGER_TOKEN` private.
4. Keep all ATS ingestions read-only. No new jobs are inserted by this feature rollout.
5. Direct ATS link checks cannot guarantee job availability. When in doubt, preserve the listing and ask the user to confirm on the employer's site.

### Registry rotation and verified partner feed

STATWORX was verified through its public Personio XML on 2026-11-08 (HTTP 200; 23 positions) and added as an enabled employer ATS source. The registry now has 20 enabled feeds. To avoid exhausting Render Free runtime, the daily read-only scanner rotates a maximum of 11 sources per execution, with wraparound: all active feeds are covered over successive days. A separate weekly, bounded directory resolver checks up to three curated public partner pages and logs candidate ATS links for human review. The weekly task does not import jobs or promote unverified feeds.
