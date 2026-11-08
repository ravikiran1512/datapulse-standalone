"""Fail-closed safeguards for the scheduled, authenticated ATS writer."""
import json

import pytest

from app.collectors import run_ingestion as worker
from app.link_checker import LinkResult
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import normalize_for_profiles


def dow_job(source_id="Dow:R2069195"):
    return RawJob(
        source_platform="Workday", source_id=source_id, company_name="Dow",
        job_title="SAP BASIS Administrator/Service specialist",
        location="Navi Mumbai (IND)",
        job_url="https://dow.wd1.myworkdayjobs.com/ExternalCareers/job/Navi-Mumbai-IND/SAP-BASIS-Administrator-Service-specialist_R2069195-1",
        description="A minimum of 5 years of SAP Basis experience with SAP HANA, Linux and SAP BTP.",
    )


def dow_board():
    return {
        "type": "workday", "company": "Dow", "enabled": True,
        "auto_import_approved": True, "max_auto_import": 5,
        "career_url": "https://dow.wd1.myworkdayjobs.com/en-US/ExternalCareers",
    }


def write_registry(tmp_path, *boards):
    path=tmp_path / "verified-boards.json"
    path.write_text(json.dumps({"boards": list(boards)}), encoding="utf-8")
    return path


def test_approved_registry_does_not_inherit_read_only_flag(tmp_path):
    unapproved={**dow_board(), "company": "Unreviewed", "auto_import_approved": False}
    disabled={**dow_board(), "company": "Disabled", "enabled": False}
    path=write_registry(tmp_path, unapproved, disabled, dow_board())
    result=worker.eligible_boards(path, approved_only=True)
    assert [b["company"] for b in result]==["Dow"]
    assert len(worker.eligible_boards(path))==2


def test_urls_are_bound_to_the_approved_employer():
    item=dow_job()
    assert worker.approved_source_url(item,dow_board())
    item.job_url="https://other.wd1.myworkdayjobs.com/ExternalCareers/job/123"
    assert not worker.approved_source_url(item,dow_board())
    item.job_url="https://dow.wd1.myworkdayjobs.com/OtherCareers/job/123"
    assert not worker.approved_source_url(item,dow_board())
    item.job_url="https://dow.wd1.myworkdayjobs.com/ExternalCareers/job/123"
    item.company_name="Not Dow"
    assert not worker.approved_source_url(item,dow_board())


def test_personio_and_smartrecruiters_host_validation():
    personio={"type":"personio", "company":"STATWORX", "board":"statworx",
              "enabled":True,"auto_import_approved":True}
    raw=RawJob(source_platform="Personio",source_id="statworx:123",company_name="STATWORX",
               job_title="Junior Data Engineer",location="Berlin, Germany",
               job_url="https://statworx.jobs.personio.de/job/123?language=en")
    assert worker.approved_source_url(raw,personio)
    raw.job_url="https://other.jobs.personio.de/job/123"
    assert not worker.approved_source_url(raw,personio)
    nagarro={"type":"smartrecruiters", "company":"Nagarro", "board":"Nagarro1",
             "enabled":True,"auto_import_approved":True}
    raw.source_platform="SmartRecruiters"
    raw.company_name="Nagarro"
    raw.job_url="https://jobs.smartrecruiters.com/Nagarro1/123"
    assert worker.approved_source_url(raw,nagarro)
    raw.job_url="https://jobs.smartrecruiters.com/Unverified/123"
    assert not worker.approved_source_url(raw,nagarro)


def test_conflicting_ats_entry_level_and_experienced_prose_is_excluded():
    """Observed on a live German SAP Basis posting: full-time != student."""
    raw=RawJob(
        source_platform="Personio",source_id="GAMBIT Consulting:324429",
        company_name="GAMBIT Consulting",job_title="SAP Basis Consultant (m/w/d)",
        location="Deutschland",job_url="https://gambit.jobs.personio.de/job/324429?language=en",
        employment_type="permanent",job_level="entry-level",structured_experience_years="1-2",
        description="Du bringst mehrjährige Erfahrung in der SAP Basis Administration mit und hast bereits komplexe SAP-Systemlandschaften betreut.",
    )
    job,reason=normalize_for_profiles(raw)
    assert job is None and reason=="experienced_prose_conflicts_with_entry_level"


@pytest.mark.asyncio
async def test_unapproved_or_fixture_production_writes_are_rejected(tmp_path):
    path=write_registry(tmp_path,dow_board())
    with pytest.raises(ValueError,match="allowlist"):
        await worker.collect(path,dry_run=False)
    with pytest.raises(ValueError,match="synthetic fixture"):
        await worker.collect(path,dry_run=False,approved_only=True,fixture_file=path)


@pytest.mark.asyncio
async def test_link_verification_and_idempotent_batch_candidate(monkeypatch,tmp_path):
    path=write_registry(tmp_path,dow_board())
    monkeypatch.setitem(worker.CONNECTORS,'workday',lambda board,fetcher:[dow_job(),dow_job()])
    checked=[]
    sent=[]
    async def check(url,client):
        checked.append(url)
        return LinkResult("live","Employer URL reachable")
    async def push(records,url,token):
        sent.extend(records)
        return {'inserted':len(records),'updated':0,'server_rejected':0}
    monkeypatch.setattr(worker,'inspect_link',check)
    monkeypatch.setattr(worker,'push_batches',push)
    result=await worker.collect(path,dry_run=False,approved_only=True,api_base_url="https://api.example.org")
    assert result['boards_attempted']==1 and result['links_verified']==1
    assert result['accepted']==1 and result['inserted']==1 and len(sent)==1
    assert result['rejected']=={'duplicate_source_job':1}
    assert len(checked)==1


@pytest.mark.asyncio
async def test_uncertain_or_closed_link_never_reaches_database(monkeypatch,tmp_path):
    path=write_registry(tmp_path,dow_board())
    monkeypatch.setitem(worker.CONNECTORS,'workday',lambda board,fetcher:[dow_job()])
    async def uncertain(url,client):
        return LinkResult("uncertain","ATS refused access")
    async def unexpected_push(*args,**kwargs):
        pytest.fail("Unverified link must not be sent to ingestion API")
    monkeypatch.setattr(worker,'inspect_link',uncertain)
    monkeypatch.setattr(worker,'push_batches',unexpected_push)
    result=await worker.collect(path,dry_run=False,approved_only=True,api_base_url="https://api.example.org")
    assert result['accepted']==0 and result['rejected']=={'link_uncertain':1}
    assert result['inserted']==0


@pytest.mark.asyncio
async def test_internal_server_persist_is_allowed_only_after_link_check(monkeypatch,tmp_path):
    path=write_registry(tmp_path,dow_board())
    monkeypatch.setitem(worker.CONNECTORS,'workday',lambda board,fetcher:[dow_job()])
    async def live(url,client):
        return LinkResult('live','Official page reachable')
    async def unexpected_api_push(*args,**kwargs):
        pytest.fail('Render-side writer must not call an external ingestion API')
    persisted=[]
    async def internal_writer(records):
        persisted.extend(records)
        return {'inserted':0,'updated':len(records)}
    monkeypatch.setattr(worker,'inspect_link',live)
    monkeypatch.setattr(worker,'push_batches',unexpected_api_push)
    result=await worker.collect(path,dry_run=False,approved_only=True,persist=internal_writer)
    assert result['accepted']==1 and result['updated']==1
    assert len(persisted)==1 and persisted[0][0].company_name=='Dow'
