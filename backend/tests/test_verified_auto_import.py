"""Safeguards for the first approved production ingestion source."""
import pytest

from app.verified_auto_import import approved_board,pilot_eligibility,import_approved_jobs
from datapulse_ingestion.models import RawJob


def sample(title="SAP BASIS Administrator/Service specialist",
           description="A minimum of 5 years relevant experience with SAP Basis applications, configuration and maintenance.",
           url="https://dow.wd1.myworkdayjobs.com/ExternalCareers/job/Navi-Mumbai-IND/SAP-BASIS-Administrator-Service-specialist_R2069195-1",
           company="Dow", location="Navi Mumbai (IND)"):
    return RawJob(source_platform="Workday",source_id=company+":R2069195",
                  company_name=company,job_title=title,location=location,
                  job_url=url,description=description)


def test_only_verified_approved_dow_board():
    registry=[
        {"company":"Dow","type":"workday","enabled":True,
         "career_url":"https://dow.wd1.myworkdayjobs.com/en-US/ExternalCareers"},
        {"company":"GAMBIT Consulting","type":"personio","board":"gambit","enabled":True}]
    board=approved_board(registry)
    assert board["company"]=="Dow"
    assert board["queries"]==["SAP Basis"]
    assert board["max_detail_jobs"]==5
    with pytest.raises(RuntimeError):
        approved_board([{"company":"Dow","type":"workday","enabled":True,
            "career_url":"https://example.org/careers"}])


@pytest.mark.parametrize("raw",[
    sample(company="Other Employer"),
    sample(url="https://example.org/careers/job/123"),
    sample(title="Director SAP Basis",description="A minimum of 5 years SAP Basis experience."),
    sample(description="At least 9 years of SAP Basis experience."),
    sample(location="Remote Worldwide"),
])
def test_pilot_fails_closed_for_unapproved_cases(raw):
    assert pilot_eligibility(raw) is None


def test_dow_five_years_qualifies():
    j=pilot_eligibility(sample())
    assert j is not None
    assert j.min_experience_years==5
    assert "sap_basis_experienced" in j.persona_tags
    assert j.country_code=="IN"


@pytest.mark.asyncio
async def test_duplicate_upsert_preserves_identity_and_does_not_duplicate():
    registry=[{"company":"Dow","type":"workday","enabled":True,
         "career_url":"https://dow.wd1.myworkdayjobs.com/en-US/ExternalCareers"}]
    stored={}
    async def upsert(pool,job,description):
        key=job.source_key
        existed=key in stored
        stored[key]={"id":"stable-id","job":job}
        return ("updated" if existed else "inserted"),"stable-id"
    def connector(board):
        assert board["queries"]==["SAP Basis"]
        return [sample(),sample()]  # repeated candidate within same ATS page
    a=await import_approved_jobs(None,registry=registry,connector=connector,upsert=upsert)
    b=await import_approved_jobs(None,registry=registry,connector=connector,upsert=upsert)
    assert a["inserted"]==1 and a["updated"]==0 and a["eligible"]==1
    assert b["inserted"]==0 and b["updated"]==1 and b["eligible"]==1
    assert len(stored)==1
