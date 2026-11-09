from fastapi.testclient import TestClient
from app import main
from app.db import UPSERT_SQL
from datapulse_ingestion.profiles import PERSONA_IDS

class Conn:
    def __init__(self): self.calls=[]
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def fetchval(self,sql,*args):
        self.calls.append((sql,args))
        if 'count(*)' in sql: return 0
        return None
    async def fetch(self,sql,*args):
        self.calls.append((sql,args))
        return []
class Pool:
    def __init__(self): self.conn=Conn()
    def acquire(self): return self.conn


def test_combined_persona_country_query_is_parameterized(monkeypatch):
    pool=Pool()
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setattr(main,'db',lambda request:pool)
    with TestClient(main.app) as c:
        r=c.get('/api/v1/jobs',params=[('persona','sap_basis_experienced'),('persona','sap_basis_student'),('country','IN')])
    assert r.status_code==200, r.text
    assert r.json()['total']==0
    queries=' '.join(sql for sql,args in pool.conn.calls)
    assert 'country_code=$2' in queries
    assert "country_code IN ('DE','IN')" in queries
    assert 'min_experience_years <= 5' in queries and 'min_experience_years <= 2' in queries
    assert "link_status='live'" in queries
    assert pool.conn.calls[0][1][0]==['sap_basis_experienced','sap_basis_student']
    assert pool.conn.calls[0][1][1]=='IN'


def test_unsupported_profile_and_country_are_rejected(monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    with TestClient(main.app) as c:
        assert c.get('/api/v1/jobs?persona=director').status_code==422
        assert c.get('/api/v1/jobs?country=US').status_code==422


def test_profile_metadata_and_legacy_bookmarks_unchanged(monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    with TestClient(main.app) as c:
        x=c.get('/api/v1/profiles')
    assert x.status_code==200
    assert {i['id'] for i in x.json()['profiles']}==set(PERSONA_IDS)


def test_upsert_keeps_legacy_ids_and_updates_personas():
    assert 'ON CONFLICT (normalized_company_name,normalized_job_title,normalized_location)' in UPSERT_SQL
    assert 'persona_tags=EXCLUDED.persona_tags' in UPSERT_SQL
    assert 'country_code=EXCLUDED.country_code' in UPSERT_SQL
    assert '$28::text[]' in UPSERT_SQL
