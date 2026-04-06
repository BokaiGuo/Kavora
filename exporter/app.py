from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import traceback

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

import uvicorn

from exporter.adapters.sglang import SGLangAdapter
from exporter.adapters.vllm import VllmAdapter
from exporter.config import Settings
from exporter.derive.compute import compute_derived
from exporter.registry.prom_writer import PromWriter

logger = logging.getLogger(__name__)

settings = Settings()
writer = PromWriter()
app = FastAPI(title="KV Cache Exporter")

_scrape_state: dict[str, object] = {
    "last_success_ts": None,
    "consecutive_failures": 0,
    "last_error": "",
}


def _build_adapter() -> VllmAdapter | SGLangAdapter:
    backend_type = (settings.backend_type or "vllm").strip().lower()
    common = dict(
        metrics_url=settings.backend_metrics_url,
        model_name=settings.model_name,
        instance_name=settings.instance_name,
        model_group=settings.model_group,
    )
    if backend_type == "sglang":
        return SGLangAdapter(**common)
    return VllmAdapter(**common)


adapter = _build_adapter()


async def poll_loop() -> None:
    while True:
        try:
            native = await adapter.collect()
            derived = compute_derived(native)
            writer.write(derived)
            _scrape_state["last_success_ts"] = time.time()
            _scrape_state["consecutive_failures"] = 0
            _scrape_state["last_error"] = ""
            writer.set_scrape_health(
                last_success_ts=float(_scrape_state["last_success_ts"]),
                consecutive_failures=0,
            )
        except Exception:
            _scrape_state["consecutive_failures"] = int(_scrape_state["consecutive_failures"]) + 1
            err = traceback.format_exc()
            _scrape_state["last_error"] = err[: settings.scrape_error_max_len]
            logger.exception("backend scrape failed")
            writer.inc_scrape_failure()
            writer.set_scrape_health(
                last_success_ts=_scrape_state["last_success_ts"],  # type: ignore[arg-type]
                consecutive_failures=int(_scrape_state["consecutive_failures"]),
            )
        await asyncio.sleep(max(settings.poll_interval_s, 0.2))


@app.on_event("startup")
async def on_startup() -> None:
    app.state.poll_task = asyncio.create_task(poll_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(app.state, "poll_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", response_model=None)
async def readyz() -> JSONResponse | dict[str, object]:
    now = time.time()
    last = _scrape_state["last_success_ts"]
    fails = int(_scrape_state["consecutive_failures"])
    stale = last is None or (now - float(last) > settings.scrape_stale_after_s)
    too_many_failures = fails >= settings.scrape_failures_not_ready
    if stale or too_many_failures:
        body = {
            "ready": False,
            "last_success_ts": last,
            "consecutive_failures": fails,
            "stale": stale,
            "too_many_failures": too_many_failures,
            "last_error": _scrape_state["last_error"],
        }
        return JSONResponse(status_code=503, content=body)
    return {"ready": True, "last_success_ts": last, "consecutive_failures": fails}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=writer.render(), media_type="text/plain; version=0.0.4; charset=utf-8")


def main() -> None:
    uvicorn.run(app, host=settings.exporter_host, port=settings.exporter_port)


if __name__ == "__main__":
    main()
