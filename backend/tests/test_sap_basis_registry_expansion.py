"""Local no-network contract tests for SAP Basis country and experience profiles."""
import asyncio
import json
from pathlib import Path

import pytest

from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import (
    SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR,
    normalize_for_profiles, parse_experience, country_of,
)
from datapulse_ingestion.connectors import smartrecruiters, workday
from app.collectors.run_ingestion import collect, resolve_profiles, bool_arg, eligible_boards
from app.db import source_type_for

ROOT = Path(__file__).resolve().parents[2]


def make_job(title, loc, desc, **extra):
    return RawJob(
        job_title=title, location=loc, company_name='Employer',
        description=desc, job_url='https://jobs.example.com/posting/123',
        source_platform='Personio', source_id='example:123', **extra,
    )


@pytest.mark.parametrize('title',[
    'SAP Basis Consultant', 'SAP NetWeaver Administrator',
    'SAP HANA Administrator', 'S/4HANA Basis Consultant',
    'SAP System Administrator', 'SAP BTP Admin',
])
def test_sap_keyword_normalization_exact_five_years(title):
    job,reason=normalize_for_profiles(make_job(title,'Bengaluru, India',
        'Requirements: Minimum 5 years of SAP Basis experience. SAP NetWeaver and HANA administration.'))
    assert reason=='accepted', (title,reason)
    assert job.country_code=='IN' and job.persona_tags==[SAP_EXPERIENCED]
    assert job.min_experience_years==5


def test_senior_sap_basis_five_years_allowed_but_executive_not():
    a,why=normalize_for_profiles(make_job('Senior SAP Basis Consultant','Frankfurt, Germany',
        '5+ years of experience in SAP Basis administration'))
    assert why=='accepted' and a.min_experience_years==5
    for title in ['Director SAP Basis','Head of SAP Basis','Principal SAP Basis Architect']:
        b,_=normalize_for_profiles(make_job(title,'Frankfurt, Germany',
            '5+ years of experience in SAP Basis'))
        assert b is None


def test_5_year_bracket_literal_and_german_ranges():
    for text,expected in [('4-6 years SAP Basis experience',(4,6)),
                          ('Mindestens 5 Jahre Berufserfahrung SAP Basis',(5,None)),
                          ('Requirements: SAP Basis 5 yrs',(5,None)),
                          ('5+ years of SAP Basis experience',(5,None))]:
        e=parse_experience(text)
        assert (e.minimum,e.maximum)==expected, (text,e)


@pytest.mark.parametrize('job_title,loc,desc,accepted',[
    ('SAP Basis Werkstudent','Munich, Germany','Student work in SAP Basis.',True),
    ('SAP Basis Intern','India','SAP Basis internships.',False),
    ('SAP FICO Consultant','Germany','Requirements: 5 years of SAP FICO.',False),
    ('Senior Data Engineer','Berlin, Germany','2 years of experience. English Python SQL Databricks Spark.',False),
    ('Junior Data Engineer','Magdeburg, Germany','3+ years of experience. English Python SQL Databricks Spark.',False),
    ('Junior Data Engineer','Magdeburg, Germany','2 years of experience. English Python SQL Databricks Spark.',True),
])
def test_three_profiles_hard_constraints(job_title,loc,desc,accepted):
    j,_=normalize_for_profiles(make_job(job_title,loc,desc))
    assert (j is not None)==accepted


def test_student_mapped_zero_without_years_gate():
    j,reason=normalize_for_profiles(make_job('SAP Basis Working Student','Berlin, Germany',
        'We provide SAP Basis training, no prior experience required.'))
    assert j and j.persona_tags==[SAP_STUDENT]
    assert j.min_experience_years==0 and j.max_experience_years is None


def test_sap_role_never_inferred_from_functional_sap_fico():
    j,reason=normalize_for_profiles(make_job('SAP SD Consultant','Pune, India',
        'Requirements: 5 years of SAP SD experience.'))
    assert j is None


def test_country_includes_english_and_local_names():
    for loc,expected in [('Bangalore, Karnataka, India','IN'),('Hyderabad, India','IN'),
                         ('Noida, India','IN'),('Remote India','IN'),('Magdeburg, Deutschland','DE'),
                         ('Berlin, Germany','DE'),('Remote Germany','DE')]:
        assert country_of(make_job('SAP Basis Consultant',loc,'5+ years experience.'))==expected
    assert country_of(make_job('SAP Basis Consultant','Remote Worldwide','5+ years experience.')) is None


def test_registry_has_verified_feeds_and_separate_unverified_portals():
    data=json.loads((ROOT/'config/verified-boards.json').read_text())
    enabled=eligible_boards(ROOT/'config/verified-boards.json')
    assert len(enabled)>=18
    assert any(b['type']=='smartrecruiters' and b['company']=='Nagarro' for b in enabled)
    assert any(b['type']=='workday' and b['company']=='Accenture' for b in enabled)
    assert any(b['type']=='personio' and b['company']=='GAMBIT Consulting' for b in enabled)
    assert len(data['discovery_targets'])>=14
    assert all('portal' in x and 'reason' in x for x in data['discovery_targets'])
    assert not any(x.get('company')=='SAP SE' for x in enabled)


def test_profile_aliases_and_explicit_dry_run_bool():
    assert resolve_profiles('sap_basis_5y,sap_basis_student,data_eng_junior')==(
        SAP_EXPERIENCED,SAP_STUDENT,DE_JUNIOR)
    assert bool_arg('true') and not bool_arg('false')
    with pytest.raises(ValueError):resolve_profiles('unknown')
    assert source_type_for('SmartRecruiters')=='direct_ats'


class SmartFetcher:
    def get(self,url,**kwargs):
        if url.endswith('/744001'):
            return {'id':'744001','name':'SAP Basis Consultant','location':{'city':'Bengaluru','country':'in','fullLocation':'Bengaluru, India'},
                    'postingUrl':'https://jobs.smartrecruiters.com/Nagarro1/744001-sap-basis-consultant',
                    'releasedDate':'2026-11-07T10:00:00Z',
                    'jobAd':{'sections':{'jobDescription':{'text':'<p>5+ years of SAP Basis experience.</p>'},
                            'qualifications':{'text':'SAP HANA, Linux and NetWeaver.'}}}}
        return {'totalFound':2,'content':[
            {'id':'744001','name':'SAP Basis Consultant','location':{'city':'Bengaluru','country':'in'}},
            {'id':'744002','name':'SAP FICO Consultant','location':{'city':'Pune','country':'in'}}]}


def test_smartrecruiters_only_uses_official_posting_detail_links():
    jobs=smartrecruiters({'type':'smartrecruiters','board':'Nagarro1','company':'Nagarro','max_pages_per_query':1,
                          'queries':['SAP Basis']},SmartFetcher())
    assert len(jobs)==1
    job,why=normalize_for_profiles(jobs[0])
    assert why=='accepted' and job.persona_tags==[SAP_EXPERIENCED]
    assert jobs[0].job_url.startswith('https://jobs.smartrecruiters.com/')


class WorkdayFetcher:
    def robots_allowed(self,url):return True
    def post(self,url,**kwargs):
        return {'total':2,'jobPostings':[
            {'title':'SAP Basis Administrator','externalPath':'/job/Frankfurt/SAP-Basis_R001','locationsText':'Frankfurt, Germany'},
            {'title':'SAP FICO Consultant','externalPath':'/job/Berlin/SAP-FICO_R002','locationsText':'Berlin, Germany'}]}
    def get(self,url,**kwargs):
        return {'jobPostingInfo':{'title':'SAP Basis Administrator','jobReqId':'R001',
                                 'location':'Frankfurt, Germany','jobDescription':'<p>5+ years SAP Basis experience. HANA.</p>'}}


def test_workday_sap_roles_and_filters_non_basis():
    jobs=workday({'type':'workday','company':'Accenture',
                  'career_url':'https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers',
                  'queries':['SAP Basis'],'max_pages_per_query':1},WorkdayFetcher())
    assert len(jobs)==1
    j,why=normalize_for_profiles(jobs[0])
    assert why=='accepted' and j.persona_tags==[SAP_EXPERIENCED]
    assert 'myworkdayjobs.com' in j.job_url


async def collect_fake_records(tmp_path,monkeypatch):
    sample=[make_job('Senior SAP Basis Consultant','Bengaluru, India','5+ years SAP Basis experience.'),
            make_job('SAP Basis Werkstudent','Munich, Germany','Student SAP Basis training.'),
            make_job('Junior Data Engineer','Berlin, Germany','2 years experience. Excellent English. Python SQL PySpark Databricks Apache Spark Azure.')]
    p=tmp_path/'boards.json';p.write_text(json.dumps({'boards':[{'type':'greenhouse','board':'ignored','company':'Employer','enabled':True}]}))
    monkeypatch.setitem(__import__('app.collectors.run_ingestion',fromlist=['CONNECTORS']).CONNECTORS,'greenhouse',lambda _b,_f:sample)
    async def no_write(*a,**kw):raise AssertionError('Production writes invoked in dry run')
    monkeypatch.setattr(__import__('app.collectors.run_ingestion',fromlist=['push_batches']),'push_batches',no_write)
    return await collect(p,dry_run=True,expand_registry=True,target_profiles='sap_basis_5y,sap_basis_student,data_eng_junior')


def test_combined_dry_run_zero_production_writes(tmp_path,monkeypatch):
    x=asyncio.run(collect_fake_records(tmp_path,monkeypatch))
    assert x['accepted']==3 and x['inserted']==x['updated']==0
    assert [x['per_profile'][p]['accepted'] for p in (SAP_EXPERIENCED,SAP_STUDENT,DE_JUNIOR)]==[1,1,1]
    assert x['boards_succeeded']==1


def test_personio_generic_entry_level_is_not_student_role():
    raw=make_job('SAP Basis Consultant (m/w/d)','Germany', '',
                 job_level='entry-level',employment_type='permanent')
    j,reason=normalize_for_profiles(raw)
    assert j is None and reason=='not_eligible_for_selected_personas'


def test_personio_real_student_contract_can_qualify():
    raw=make_job('SAP Basis Consultant (m/w/d)','Germany',
                 'Learn SAP Basis Administration.',employment_type='working student')
    j,reason=normalize_for_profiles(raw)
    assert reason=='accepted' and j.persona_tags==[SAP_STUDENT]


def test_personio_german_xml_fallback_does_not_invent_text():
    from datapulse_ingestion.connectors import personio
    en='''<positions><position><id>7</id><name>SAP Basis Consultant</name><office>Germany</office><seniority>entry-level</seniority></position></positions>'''
    de='''<positions><position><id>7</id><name>SAP Basis Consultant</name><office>Germany</office><jobDescriptions><jobDescription><value>Mindestens 5 Jahre Berufserfahrung in SAP Basis und HANA.</value></jobDescription></jobDescriptions></position></positions>'''
    class Feed:
        def __init__(self):self.calls=[]
        def get(self,url,**kwargs):
            lang=kwargs['params']['language'];self.calls.append(lang)
            return {'en':en,'de':de}[lang]
    f=Feed()
    jobs=personio({'board':'firm','company':'Example',
                  'apply_url_template':'https://firm.jobs.personio.de/job/{id}?language=en'},f)
    assert len(jobs)==1 and '5 Jahre' in jobs[0].description
    assert f.calls==['en','de']
    job,reason=normalize_for_profiles(jobs[0])
    assert reason=='accepted' and job.persona_tags==[SAP_EXPERIENCED]
