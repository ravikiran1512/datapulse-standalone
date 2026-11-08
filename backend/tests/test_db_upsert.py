import asyncio
from uuid import uuid4
from app.db import upsert_job
from datapulse_ingestion.models import Job

class Scope:
    def __init__(self,result):self.result=result
    async def __aenter__(self):return self.result
    async def __aexit__(self,*a):pass
class Conn:
    def __init__(self,old=None):self.old=old;self.id=old or uuid4();self.log=[]
    def transaction(self):return Scope(self)
    async def fetchval(self,sql,*args):return self.old
    async def fetchrow(self,sql,*args):
        self.log.append((sql,args));return {'id':self.id}
    async def execute(self,sql,*args):self.log.append((sql,args));return 'OK'
class Pool:
    def __init__(self,conn):self.conn=conn
    def acquire(self):return Scope(self.conn)

def job():
    return Job(job_title='Junior Data Engineer',company_name='Example GmbH',location='Berlin, Germany',
      city='Berlin',work_model='Hybrid',employment_type='Full-time',job_level='Junior',
      domain_category='Data Engineering',language_required='English',
      tech_stack=['Python','SQL','PySpark','Databricks','Apache Spark','Azure','Delta Lake','ADLS Gen2'],
      job_url='https://example.com/careers/123',source_platform='Greenhouse',date_posted='2026-11-01',
      datapulse_fit_score='92%',source_key='Greenhouse:example:123')

def test_insert_then_update_stable_id():
    first=Conn()
    r=asyncio.run(upsert_job(Pool(first),job(),'English; SQL Python Databricks'))
    assert r==('inserted',str(first.id))
    assert first.log[0][1][18]=='direct_ats'
    second=Conn(first.id)
    r2=asyncio.run(upsert_job(Pool(second),job()))
    assert r2==('updated',str(first.id))
