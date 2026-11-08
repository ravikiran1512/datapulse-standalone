from fastapi.testclient import TestClient
from app.main import app


def test_health_reports_setup_without_database(monkeypatch):
 monkeypatch.delenv('DATABASE_URL',raising=False)
 with TestClient(app) as c:
 r=c.get('/api/v1/health')
 assert r.status_code==200
 assert r.json()=={'status':'setup_required','database_connected':False}


def test_unauthorized_ingestion_denied(monkeypatch):
 monkeypatch.setenv('INGEST_TOKEN','a'*48)
 monkeypatch.delenv('DATABASE_URL',raising=False)
 with TestClient(app) as c:
 r=c.post('/api/v1/internal/ingest',json={'jobs':[{}]})
 assert r.status_code==401


def test_invalid_search_no_database(monkeypatch):
 monkeypatch.delenv('DATABASE_URL',raising=False)
 with TestClient(app) as c:
 r=c.get('/api/v1/jobs')
 assert r.status_code==503
