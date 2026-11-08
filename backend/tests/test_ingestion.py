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
