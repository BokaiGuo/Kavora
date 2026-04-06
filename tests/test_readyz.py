import time

from fastapi.testclient import TestClient

from exporter import app as app_module


def test_healthz_alive() -> None:
    client = TestClient(app_module.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_returns_503_when_stale() -> None:
    app_module._scrape_state["last_success_ts"] = None
    app_module._scrape_state["consecutive_failures"] = 5
    app_module._scrape_state["last_error"] = "x"
    client = TestClient(app_module.app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["too_many_failures"] is True


def test_readyz_returns_200_when_fresh() -> None:
    app_module._scrape_state["last_success_ts"] = time.time()
    app_module._scrape_state["consecutive_failures"] = 0
    app_module._scrape_state["last_error"] = ""
    client = TestClient(app_module.app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
