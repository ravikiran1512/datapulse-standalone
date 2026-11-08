"""Authenticated scheduler keeps approved writes separate from read-only discovery."""
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import scheduled_scan


def test_unauthed_scan_denied(monkeypatch):
    monkeypatch.setenv('SCAN_TRIGGER_TOKEN', 'x'*48)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with TestClient(app) as client:
        assert client.post('/api/v1/internal/scan').status_code == 401
        assert client.post('/api/v1/internal/scan', headers={'Authorization': 'Bearer bad'}).status_code == 401


def test_authed_scan_does_not_run_without_db(monkeypatch):
    monkeypatch.setenv('SCAN_TRIGGER_TOKEN', 'x'*48)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with TestClient(app) as client:
        response = client.post('/api/v1/internal/scan', headers={'Authorization': 'Bearer ' + 'x'*48})
        assert response.status_code == 503
        assert response.json()['detail'] == 'Database not configured'


@pytest.mark.asyncio
async def test_approved_writer_and_discovery_have_separate_audit_runs(monkeypatch):
    monkeypatch.setenv('ENABLE_VERIFIED_AUTO_IMPORT','true')
    monkeypatch.setenv('ENABLE_STALE_CHECKS','false')
    monkeypatch.setenv('ENABLE_PARTNER_DISCOVERY','false')
    calls=[]

    class FakePool:
        async def fetchval(self,sql,*args):
            calls.append(('insert_run',sql,args))
            return 'approved-run-id'
        async def execute(self,sql,*args):
            calls.append(('update_run',sql,args))

    async def fake_collect(_source_file,**kwargs):
        if kwargs['dry_run'] is False:
            assert kwargs['approved_only'] is True
            persisted=await kwargs['persist']([('raw-job','normalized-job')])
            return {'boards_attempted':4,'boards_succeeded':4,'jobs_seen':104,
                    'accepted':1,'inserted':persisted['inserted'],'updated':persisted['updated'],
                    'rejected':{'above_target_level':10},'errors':{}}
        assert kwargs['dry_run'] is True and kwargs.get('persist') is None
        return {'boards_attempted':11,'boards_succeeded':11,'jobs_seen':780,
                'accepted':2,'rejected':{'not_target_role':778},'errors':{}}

    async def fake_persist(_pool,records):
        assert records==[('raw-job','normalized-job')]
        return {'inserted':0,'updated':1}

    monkeypatch.setattr(scheduled_scan,'collect',fake_collect)
    monkeypatch.setattr(scheduled_scan,'persist_approved_batch',fake_persist)
    await scheduled_scan._execute_scan(FakePool(),'discovery-run-id')
    assert len([item for item in calls if item[0]=='insert_run'])==1
    updates=[item for item in calls if item[0]=='update_run']
    assert len(updates)==2
    assert any('approved-run-id' in item[2] for item in updates)
    assert any('discovery-run-id' in item[2] for item in updates)


@pytest.mark.asyncio
async def test_disabled_writer_never_imports(monkeypatch):
    monkeypatch.setenv('ENABLE_VERIFIED_AUTO_IMPORT','false')
    monkeypatch.setenv('ENABLE_STALE_CHECKS','false')
    monkeypatch.setenv('ENABLE_PARTNER_DISCOVERY','false')
    class FakePool:
        async def fetchval(self,sql,*args):
            pytest.fail('Writer run must not be created')
        async def execute(self,sql,*args):
            return None
    async def fake_collect(_source_file,**kwargs):
        assert kwargs['dry_run'] is True
        return {'boards_attempted':1,'boards_succeeded':1,'jobs_seen':0,
                'accepted':0,'rejected':{},'errors':{}}
    monkeypatch.setattr(scheduled_scan,'collect',fake_collect)
    await scheduled_scan._execute_scan(FakePool(),'discovery-run-id')


@pytest.mark.asyncio
async def test_render_persist_preserves_source_description_and_counts(monkeypatch):
    calls=[]
    async def fake_upsert(pool,job,description):
        calls.append((job,description))
        return ('inserted' if job=='first' else 'updated','stable-uuid')
    monkeypatch.setattr(scheduled_scan,'upsert_job',fake_upsert)
    records=[(SimpleNamespace(description='first official description'),'first'),
             (SimpleNamespace(description='second official description'),'second')]
    result=await scheduled_scan.persist_approved_batch(object(),records)
    assert result=={'inserted':1,'updated':1}
    assert calls==[('first','first official description'),('second','second official description')]
