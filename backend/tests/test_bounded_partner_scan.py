"""No-network contract tests for bounded partner verification and ATS formats."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import httpx
import pytest

from app.collectors.ats_resolver import (
    BoundedVerifier, parse_jsonld_jobs, observed_vendor_links,
    HTTP_TIMEOUT, public_https,
)
from app.collectors.bounded_partner_scan import (
    strict_preproduction_job, scan_bounded, PROFILES,
)
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR


def job(title, place, desc, **extra):
    return RawJob(source_platform="Personio",source_id="test:id",company_name="STATWORX",
                  job_title=title,location=place,
                  job_url="https://statworx.jobs.personio.de/job/123",
                  description=desc, **extra)


@pytest.mark.parametrize("title,location,description,expected", [
    ("Senior SAP Basis Consultant","Bengaluru, India",
     "Requirements: 3-6 years of experience with SAP Basis and HANA.", SAP_EXPERIENCED),
    ("SAP HANA Administrator","Frankfurt, Germany",
     "Required 4-6 years of SAP Basis administration.", SAP_EXPERIENCED),
    ("SAP Basis Consultant","Pune, India",
     "5+ years SAP Basis experience.", SAP_EXPERIENCED),
    ("SAP Basis Consultant","Berlin, Germany",
     "7-8 years SAP Basis experience.", None),
    ("SAP Basis Junior","Berlin, Germany",
     "SAP NetWeaver service administration.",SAP_STUDENT),
    ("Werkstudent SAP Basis","Bengaluru, India",
     "Student contract SAP Basis.",SAP_STUDENT),
    ("SAP Basis Intern","Berlin, Germany",
     "Administration. Previous experience is optional.",SAP_STUDENT),
    ("Junior Data Engineer","Berlin, Germany",
     "2 years of experience. Excellent English. Python SQL Apache Spark Databricks Delta Lake.",DE_JUNIOR),
    ("Junior Data Engineer","Berlin, Germany",
     "3+ years of experience. Excellent English. Python SQL Databricks.",None),
    ("Junior Data Engineer","India",
     "1-2 years of experience. Excellent English. Python SQL Databricks.",DE_JUNIOR),
    ("SAP FICO Consultant","Pune, India",
     "3-6 years of SAP FICO experience.",None),
])
def test_three_partner_profile_boundaries(title,location,description,expected):
    selected,reason,evidence = strict_preproduction_job(job(title,location,description))
    assert (selected[0] if selected else None) == expected, (title,reason,evidence)


def test_jsonld_jobposting_employer_and_expiry_filters():
    src = {"@context":"https://schema.org","@type":"JobPosting",
           "title":"Werkstudent SAP Basis",
           "description":"<p>Work with SAP Basis systems and monitoring.</p>",
           "hiringOrganization":{"@type":"Organization","name":"STATWORX"},
           "jobLocation":{"@type":"Place","address":{
                "@type":"PostalAddress","addressLocality":"Berlin","addressCountry":"DE"}},
           "datePosted":"2026-11-07","url":"https://jobs.example.com/123"}
    html='<script type="application/ld+json">'+json.dumps(src)+'</script>'
    rows=parse_jsonld_jobs(html,"https://statworx.jobs.personio.de/job/123","STATWORX")
    assert len(rows)==1 and rows[0].country_code=="DE"
    assert rows[0].job_url=="https://statworx.jobs.personio.de/job/123"
    expired={**src,"validThrough":"2020-01-01"}
    assert not parse_jsonld_jobs('<script type="application/ld+json">'+json.dumps(expired)+'</script>',
                                 "https://statworx.jobs.personio.de/job/123","STATWORX")


def test_vendor_resolver_never_guesses_company_slug():
    url="https://career.example.com/jobs"
    assert observed_vendor_links("<h1>Software jobs</h1>",url)==[]
    found=observed_vendor_links(
        '<a href="https://recruiter.softgarden.io/job/5">Apply</a>'
        '<a href="https://careers.successfactors.com/sfcareer/jobreq">SAP</a>',url)
    assert {x["type"] for x in found}=={"softgarden","successfactors"}
    assert not public_https("http://localhost/abc")
    assert not public_https("https://127.0.0.1/jobs")


@pytest.mark.asyncio
async def test_softgarden_and_successfactors_only_public_structured_json():
    def handler(request):
        path=request.url.path
        if path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /\n")
        if path=="/api/rest/v2/jobpostings":
            return httpx.Response(200,json={"jobpostings":[
                 {"id":"12","title":"SAP Basis Intern","location":"Berlin"}]})
        if path=="/sfcareer/jobreq":
            return httpx.Response(401,text="Authentication required")
        return httpx.Response(200,html="<html>Sign in</html>",
                              headers={"content-type":"text/html"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        v=BoundedVerifier(client,asyncio.Semaphore(10))
        a=await v.verify_vendor({"type":"softgarden","url":"https://acme.softgarden.io/jobs/12"})
        b=await v.verify_vendor({"type":"successfactors",
                                  "url":"https://careers.successfactors.com/sfcareer/jobreq"})
        assert a["status"]=="verified_public_json_jobs" and len(a["jobs"])==1
        assert b["status"]=="authentication_or_access_required"


@pytest.mark.asyncio
async def test_global_http_concurrency_is_ten_maximum():
    active=0
    maximum=0
    async def handler(request):
        nonlocal active,maximum
        active+=1;maximum=max(maximum,active)
        try:
            await asyncio.sleep(0.01)
            if request.url.path=="/robots.txt":
                return httpx.Response(200,text="User-agent: *\nAllow: /\n")
            return httpx.Response(200,text="<html>OK</html>",
                                  headers={"content-type":"text/html"})
        finally:
            active-=1
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        v=BoundedVerifier(client,asyncio.Semaphore(10))
        tasks=[v.fetch(f"https://company{i}.example.org/careers")
               for i in range(25)]
        responses=await asyncio.gather(*tasks)
    assert all(status=="ok" for _,status in responses)
    assert maximum<=10
    assert HTTP_TIMEOUT==5.0


@pytest.mark.asyncio
async def test_multi_partner_dry_run_no_database(monkeypatch):
    async def handler(request):
        if request.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /\n")
        if request.url.host=="www.databricks.com":
            return httpx.Response(200,text="<html><h3>STATWORX</h3></html>",
                headers={"content-type":"text/html"})
        if request.url.path=="/":
            return httpx.Response(200,text="<html><a href='https://statworx.jobs.personio.de/job/123'>Job</a></html>",
                headers={"content-type":"text/html"})
        return httpx.Response(404)
    target={"name":"STATWORX","ecosystem":"databricks","career_page":
            "https://statworx.jobs.personio.de/","country":"DE"}
    board={"type":"personio","board":"statworx","company":"STATWORX","enabled":True}
    def fake_connector(_board):
        return [
            job("Werkstudent SAP Basis","Berlin, Germany","SAP systems."),
            job("Senior Data Engineer","Berlin, Germany","5+ years Python SQL Spark"),
            job("Senior SAP Basis Administrator","Pune, India",
                "Required 3-6 years of SAP Basis experience.")
        ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await scan_bounded(
            [target],[board],sources={"databricks_partners","softgarden","successfactors"},
            profiles=PROFILES,max_partners=1,max_feeds=1,
            request_client=client,connector_runner=fake_connector)
    assert result["dry_run"] and result["inserted"]==0 and result["updated"]==0
    assert result["jobs_seen"]==3 and result["accepted"]==2
    assert result["per_profile"][SAP_EXPERIENCED]==1
    assert result["per_profile"][SAP_STUDENT]==1
    assert result["per_profile"][DE_JUNIOR]==0
