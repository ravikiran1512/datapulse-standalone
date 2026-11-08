"""No database writes can be triggered by the alternative free scheduler."""
from fastapi.testclient import TestClient
from app.main import app


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
