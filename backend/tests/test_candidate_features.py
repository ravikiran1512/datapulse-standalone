import pytest
from datapulse_ingestion.metadata import extract_metadata,match_breakdown
from datapulse_ingestion.profiles import Experience,persona_matches,DE_JUNIOR,SAP_EXPERIENCED
from datapulse_ingestion.models import RawJob
from app.link_checker import safe_ats_url, inspect_link, LinkResult
from app.collectors.discover_partners import classify_ats,linked_sources
from app.main import app
from fastapi.testclient import TestClient


def raw(title,location='Berlin, Germany',description='Python SQL PySpark English required.'):
    return RawJob(source_platform='Personio',source_id='example:001',job_title=title,
        company_name='Example',location=location,description=description,job_url='https://example.jobs.personio.de/job/1')


def test_salaries_eur_and_inr():
    eur=extract_metadata('€55.000 - €70.000 per annum')
    inr=extract_metadata('Salary ₹12 - ₹18 LPA')
    assert eur.salary_range=='€55,000–€70,000' and eur.salary_currency=='EUR'
    assert inr.salary_range=='₹12–₹18 LPA' and inr.salary_currency=='INR'


def test_visa_and_language_conservative():
    x=extract_metadata('International team. Relocation assistance available.')
    assert not x.visa_supported and x.relocation_assistance and not x.english_friendly
    y=extract_metadata('Excellent English communication. Visa sponsorship offered.')
    assert y.visa_supported and y.english_friendly
    z=extract_metadata('No visa sponsorship available. English required.')
    assert not z.visa_supported


@pytest.mark.parametrize('description,expected',[
    ('No relocation support is provided for this role.', False),
    ('Relocation assistance not available.', False),
    ('Relocation support is not offered.', False),
    ('We do not provide relocation assistance.', False),
    ('Relocation assistance available.', True),
    ('We cover your relocation.', True),
])
def test_relocation_requires_affirmative_evidence(description,expected):
    assert extract_metadata(description).relocation_assistance is expected


def test_fit_breakdown_not_claim_personal_resume():
    b=match_breakdown('Data Engineering',['Python','SQL'],[DE_JUNIOR],0,2)
    assert b['matched_skills']==['Python','SQL'] and 'Databricks' in b['missing_or_recommended_skills']
    assert 'not a personal resume' in b['method']


def test_de_junior_rejects_explicit_range_0_to_5():
    assert not persona_matches(raw('Junior Data Engineer'),'DE',Experience(0,5,'0-5 years'))


def test_sap_experienced_strict_3_to_6():
    r=raw('Senior SAP Basis Consultant','Bengaluru, India')
    assert SAP_EXPERIENCED in persona_matches(r,'IN',Experience(5,6,'5-6 years'))
    assert not persona_matches(r,'IN',Experience(5,8,'5-8 years'))


def test_no_persona_by_default_means_three_filtered_tracks():
    from inspect import signature
    from app.main import jobs
    p=signature(jobs).parameters['persona']
    assert p.default.default==[]


def test_partner_resolver_never_invents_ats_slug():
    html='<a href="https://statworx.jobs.personio.de/">Jobs</a><a href="https://test.example/career">Other</a>'
    result=linked_sources(html,'https://www.statworx.com/jobs')
    assert len(result)==1 and result[0]['ats']=='personio'
    assert classify_ats('https://www.unknown-consulting.com/jobs') is None


@pytest.mark.parametrize('link,expected',[
 ('https://statworx.jobs.personio.de/job/123',True),
 ('https://dow.wd1.myworkdayjobs.com/ExternalCareers/job/1',True),
 ('https://127.0.0.1/status',False),
 ('http://example.jobs.personio.de/xml',False),
 ('https://foo.jobs.personio.de.evil.example/internal',False),
 ('https://user:password@statworx.jobs.personio.de/job/1',False),
 ('https://www.randomcompany.com/careers/job-1',False),
])
def test_restricted_link_hosts(link,expected):
    assert safe_ats_url(link)==expected


class FakeResponse:
    def __init__(self,status,text='',headers=None):
        self.status_code=status;self.text=text;self.headers=headers or {'content-type':'text/html'}
class FakeClient:
    def __init__(self,head,gets):self._head=head;self._get=gets
    async def head(self,*args,**kwargs):return FakeResponse(self._head)
    async def get(self,*args,**kwargs):return FakeResponse(self._get)


@pytest.mark.asyncio
async def test_head_404_but_get_200_is_live():
    result=await inspect_link('https://statworx.jobs.personio.de/job/42',FakeClient(404,200))
    assert result.status=='live'


@pytest.mark.asyncio
async def test_confirmed_404_requires_get():
    result=await inspect_link('https://statworx.jobs.personio.de/job/42',FakeClient(404,404))
    assert result.status=='confirmed_closed'


def test_application_patch_requires_authorization():
    t=TestClient(app)
    resp=t.patch('/api/v1/bookmarks/00000000-0000-0000-0000-000000000001/status',json={'status':'Applied'})
    assert resp.status_code in (401,503)
    from app.main import ApplicationStatusUpdate
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ApplicationStatusUpdate(status='Hired')
