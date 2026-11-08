"""Contract tests for read-only SAP/Databricks official partner discovery."""
import asyncio
from pathlib import Path

import httpx
import pytest
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR
from app.collectors import partner_directories as d


def raw(title, loc, description, **fields):
    return RawJob(
        source_platform="Personio", source_id="p:123",
        company_name="Example Co", job_title=title, location=loc,
        job_url="https://example.org/official-application/123",
        description=description, **fields)


def test_only_actual_ats_links_generate_board_tokens():
    assert d.ats_board_from_link("https://careers.example.com/jobs", "Example") is None
    assert d.ats_board_from_link("http://jobs.lever.co/example/123", "Example") is None
    assert d.ats_board_from_link("https://example.jobs.personio.de/job/123", "Example")["board"] == "example"
    assert d.ats_board_from_link("https://boards.greenhouse.io/acme/jobs/123", "Example")["board"] == "acme"
    assert d.ats_board_from_link("https://jobs.lever.co/acme/abc", "Example")["board"] == "acme"
    assert d.ats_board_from_link("https://jobs.ashbyhq.com/acme/abc", "Example")["board"] == "acme"
    assert d.ats_board_from_link("https://jobs.smartrecruiters.com/Acme/123", "Example")["board"] == "Acme"
    assert d.ats_board_from_link("https://company.wd5.myworkdayjobs.com/en-US/ExtJobs/job/abc", "Example")["type"] == "workday"
    assert d.ats_board_from_link("https://company.softgarden.io/job/123", "Example") is None


def test_directory_page_evidence_is_not_certification_claim():
    assert d.directory_evidence("<h3>NTT DATA Business Solutions</h3>", "NTT DATA Business Solutions")
    assert not d.directory_evidence("<p>Loading...</p>", "NTT DATA Business Solutions")
    assert not d.directory_evidence("<script>const name='NTT DATA Business Solutions'</script>", "NTT DATA Business Solutions")
    assert d.directory_url("sap", "NTT DATA") == "https://partnerfinder.sap.com/partnerNavigator?q=NTT%20DATA"


def test_partner_ats_links_require_employer_page_evidence():
    html = '<a href="https://statworx.jobs.personio.de/job/123">Apply</a>'
    rows = d.ats_links(html, "https://statworx.com/careers")
    assert len(rows) == 1 and rows[0]["type"] == "personio"


def test_round_robin_ecosystem_selection():
    rows = [
        {"ecosystem":"sap","name":"A"}, {"ecosystem":"sap","name":"B"},
        {"ecosystem":"databricks","name":"C"},{"ecosystem":"databricks","name":"D"}]
    assert [x["name"] for x in d.select_targets(rows, {"sap","databricks"},3)] == ["A","C","B"]


@pytest.mark.parametrize("job,expected,reason", [
    (raw("Senior SAP Basis Administrator","Bengaluru, India",
         "Required 4-6 years of experience in SAP Basis and HANA."), SAP_EXPERIENCED, "accepted_experienced"),
    (raw("SAP Basis Administrator","Frankfurt, Germany",
         "Required 4-7 years of experience with SAP Basis."), SAP_EXPERIENCED, "accepted_experienced"),
    (raw("SAP Basis Administrator","Berlin, Germany",
         "At least 5 years of experience in SAP Basis."), None, "sap_experience_range_unverified"),
    (raw("SAP Basis Consultant","Pune, India",
         "Required 2-3 years of experience in SAP Basis."), None, "sap_experience_outside_4_to_7"),
    (raw("Director SAP Basis","Pune, India",
         "Required 4-6 years of experience in SAP Basis."), None, "executive_or_principal_title"),
    (raw("SAP FICO Consultant","Bengaluru, India",
         "Required 4-6 years experience in SAP FICO."), None, "unrelated_title"),
    (raw("Werkstudent SAP Basis","Munich, Germany",
         "SAP systems support, student contract."), SAP_STUDENT, "accepted_student"),
    (raw("SAP Systems Internship","Berlin, Germany",
         "Intern SAP systems administration."), SAP_STUDENT, "accepted_student"),
    (raw("SAP Basis Intern","Hyderabad, India",
         "SAP Basis internship."), None, "sap_student_non_germany"),
    (raw("SAP Basis Consultant","Berlin, Germany",
         "Required 1-2 years of experience, full time.", job_level="entry-level"), None,
         "sap_experience_outside_4_to_7"),
    (raw("Junior Data Engineer","Berlin, Germany",
         "1-2 years of experience. Excellent English. Python SQL Databricks Spark Azure Delta Lake."),
         DE_JUNIOR, "accepted_de_junior"),
    (raw("Junior Data Engineer","Berlin, Germany",
         "3+ years of experience. Excellent English. Python SQL Databricks Apache Spark."),
         None, "de_requires_3_plus_years"),
    (raw("Junior Data Engineer","Berlin, Germany",
         "2 years of experience. Excellent English. Python SQL Databricks Apache Spark Azure Delta Lake."),
         DE_JUNIOR, "accepted_de_junior"),
    (raw("Junior Data Engineer","Pune, India",
         "1-2 years of experience. Excellent English. Python SQL Databricks Spark."),
         None, "data_engineering_non_germany"),
])
def test_strict_profiles(job, expected, reason):
    profiles, why, evidence = d.strict_partner_profiles(job)
    assert (profiles[0] if profiles else None) == expected
    assert why == reason, (profiles, why, evidence)


def test_partner_command_is_dry_run_only_and_has_no_database_import():
    source = Path(d.__file__).read_text()
    assert "INSERT INTO datapulse" not in source
    assert "DATABASE_URL" not in source
    assert '"dry_run": True' in source


@pytest.mark.asyncio
async def test_dryrun_fetches_partner_careers_and_never_persists(monkeypatch):
    target = [{"ecosystem":"databricks","name":"STATWORX", "country":"DE",
               "career_page":"https://statworx.jobs.personio.de/"}]
    robots = "User-agent: *\nAllow: /\n"
    def handler(req):
        host=req.url.host
        if req.url.path == "/robots.txt":
            return httpx.Response(200,text=robots)
        if host == "www.databricks.com":
            return httpx.Response(200,text="<html><h3>STATWORX</h3></html>",
                                  headers={"content-type":"text/html"})
        if host == "statworx.jobs.personio.de":
            return httpx.Response(200,
                text='<html><a href="https://statworx.jobs.personio.de/job/123">Apply</a></html>',
                headers={"content-type":"text/html"})
        return httpx.Response(404)
    class FakeFetcher:
        class Client:
            def close(self):
                pass
        client=Client()
    monkeypatch.setitem(d.CONNECTORS,"personio",
        lambda board,fetcher: [raw("Werkstudent SAP Basis", "Berlin, Germany", "SAP systems support.")])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await d.scan_partner_targets(
          target,[{"type":"personio","company":"STATWORX","board":"statworx","enabled":True}],
          ecosystems={"databricks"},max_partners=1,max_feeds=1,
          request_client=client,connector_fetcher=FakeFetcher())
    assert result["dry_run"] is True and result["inserted"] == 0
    assert result["per_profile"][SAP_STUDENT] == 1
    assert result["feeds_validated"] == 1
    assert result["companies"][0]["directory_status"] == "name_visible_on_public_page"


def test_structured_feed_discovery_is_only_an_unverified_candidate():
    html = '<link rel="alternate" type="application/rss+xml" href="/careers/jobs.rss">'
    rows = d.ats_links(html, "https://careers.example.org/")
    assert len(rows) == 1
    assert rows[0]["type"] == "structured_feed_unverified"
    # Never guess this is a SuccessFactors or Softgarden job feed.
    assert d.ats_board_from_link(rows[0]["url"], "Example") is None
