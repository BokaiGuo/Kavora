import asyncio
import json
import time

from fastapi.routing import APIRoute

from exporter import app as app_module


def _endpoint(app, path: str):
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def test_healthz_alive() -> None:
    app = app_module.create_app(start_poll_loop=False)
    endpoint = _endpoint(app, "/healthz")

    resp = asyncio.run(endpoint())

    assert resp == {"status": "ok"}


def test_readyz_returns_503_when_stale() -> None:
    app = app_module.create_app(start_poll_loop=False)
    app.state.scrape_state["last_success_ts"] = None
    app.state.scrape_state["consecutive_failures"] = 5
    app.state.scrape_state["last_error"] = "x"
    endpoint = _endpoint(app, "/readyz")

    resp = asyncio.run(endpoint())

    assert resp.status_code == 503
    body = json.loads(resp.body)
    assert body["ready"] is False
    assert body["too_many_failures"] is True


def test_readyz_returns_200_when_fresh() -> None:
    app = app_module.create_app(start_poll_loop=False)
    app.state.scrape_state["last_success_ts"] = time.time()
    app.state.scrape_state["consecutive_failures"] = 0
    app.state.scrape_state["last_error"] = ""
    endpoint = _endpoint(app, "/readyz")

    resp = asyncio.run(endpoint())

    assert resp["ready"] is True
