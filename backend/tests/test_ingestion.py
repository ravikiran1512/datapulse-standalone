from app.db import identity,source_type_for,validate_url,UPSERT_SQL
from datapulse_ingestion.models import Job
from datapulse_ingestion.ingestion import validate_for_write
from datapulse_ingestion.matching import skill_cosine
from datapulse_ingestion.policy import RawJob,normalize_job
import pytest

def test_normalized_identity():
    assert identity('München - München')=='munchen munchen'
    assert identity('  ACME GmbH ')=='acme gmbh'

def test_source_category():
    assert source_type_for('Greenhouse')=='direct_ats'
    assert source_type_for('LinkedIn')=='job_board'
    assert 'ON CONFLICT' in UPSERT_SQL and 'official_apply_url' in UPSERT_SQL

@pytest.mark.parametrize('url',['http://example.com','https://localhost/private','https://user:pass@example.com'])
def test_reject_bad_urls(url):
    with pytest.raises(ValueError):validate_url(url)

def test_job_threshold_rejects_role():
    tags=['Python','SQL','PySpark','Databricks','Apache Spark','Azure','ADLS Gen2','Delta Lake']
    j=Job(job_title='Senior Data Engineer',company_name='Example',location='Berlin, Germany',city='Berlin',
      work_model='Hybrid',employment_type='Full-time',job_level='Senior',domain_category='Data Engineering',
      language_required='English',tech_stack=tags,job_url='https://example.com/job/1',source_platform='Greenhouse',
      date_posted=None,datapulse_fit_score='90%',source_key='gh:123')
    assert validate_for_write(j).reason=='excluded_seniority'


def test_cosine_range():
    assert 0<=skill_cosine(['Python','SQL','Databricks'])<=1
    assert skill_cosine(['Python','SQL','PySpark','Databricks','Apache Spark','Azure','Delta Lake','ADLS Gen2'])>.8


def test_excludes_senior_experience_hidden_in_mid_title():
    raw = RawJob(
        source_platform='Personio', source_id='jedox:2700918', company_name='Jedox',
        job_title='Data Engineer (m/f/d)', location='Germany remote',
        job_url='https://jedox.jobs.personio.de/job/2700918?language=en',
        description='7+ years in data engineering. Excellent English communication skills are required. Python SQL Spark Delta Lake Azure.',
        tech_stack=['Python','SQL','Apache Spark','Delta Lake','Azure'],
    )
    job, reason = normalize_job(raw, {'filters':{'exclude_senior':True}})
    assert job is None and reason == 'above_target_level'


def test_excludes_mandatory_very_good_german():
    raw = RawJob(
        source_platform='Personio', source_id='woodmark:1039640', company_name='Woodmark',
        job_title='Cloud Data Engineer (m/f/d)', location='Germany',
        job_url='https://woodmark-consulting-ag.jobs.personio.de/job/1039640?language=en',
        description='Very good German and English skills are required. Python SQL Spark Databricks.',
        tech_stack=['Python','SQL','Apache Spark','Databricks'],
    )
    job, reason = normalize_job(raw, {'filters':{'english_friendly_only':True}})
    assert job is None and reason == 'english_requirement_unverified_or_german_mandatory'
