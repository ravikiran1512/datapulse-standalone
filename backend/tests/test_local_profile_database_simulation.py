"""Ephemeral SQLite mirror test: NOT a production PostgreSQL insertion."""
import sqlite3
from uuid import uuid4
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import normalize_for_profiles


def test_local_insert_upsert_bookmarks_stable_all_profiles():
    db=sqlite3.connect(':memory:')
    db.executescript('''
      CREATE TABLE jobs(id text primary key,company text,title text,location text,
                        country_code text,experience_min integer,experience_max integer,
                        persona text,apply_url text,
                        UNIQUE(company,title,location));
      CREATE TABLE saved(job_id text primary key);
    ''')
    rows=[
      ('Senior SAP Basis Consultant','India','5+ years SAP Basis experience.','sap_basis_experienced'),
      ('SAP Basis Intern','Germany','SAP Basis technical internship.','sap_basis_student'),
      ('Junior Data Engineer','Berlin, Germany','1 year experience. Excellent English. Python SQL PySpark Databricks Apache Spark Azure.','data_engineering_junior'),
    ]
    first_ids=[]
    for title,location,desc,expected in rows:
        raw=RawJob(source_platform='SmartRecruiters',source_id=title,company_name='Example',
            job_title=title,location=location,description=desc,job_url='https://jobs.smartrecruiters.com/Example/123')
        job,reason=normalize_for_profiles(raw)
        assert reason=='accepted' and job.persona_tags==[expected]
        identifier=str(uuid4());first_ids.append(identifier)
        db.execute('''INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(company,title,location) DO UPDATE SET experience_min=excluded.experience_min''',
             (identifier,'Example',title,job.location,job.country_code,job.min_experience_years,
              job.max_experience_years,expected,job.job_url))
    db.execute('INSERT INTO saved VALUES (?)',(first_ids[0],))
    for title,location,desc,expected in rows:
        raw=RawJob(source_platform='SmartRecruiters',source_id=title,company_name='Example',
            job_title=title,location=location,description=desc,job_url='https://jobs.smartrecruiters.com/Example/123')
        job,_=normalize_for_profiles(raw)
        db.execute('''INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(company,title,location) DO UPDATE SET apply_url=excluded.apply_url''',
             (str(uuid4()),'Example',title,job.location,job.country_code,job.min_experience_years,
              job.max_experience_years,expected,job.job_url))
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==3
    assert db.execute('SELECT job_id FROM saved').fetchone()[0]==first_ids[0]
    assert db.execute('SELECT id FROM jobs ORDER BY rowid').fetchall()==[(i,) for i in first_ids]
