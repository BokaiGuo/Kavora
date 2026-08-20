from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import traceback
import json
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

import uvicorn

from exporter.adapters.sglang import SGLangAdapter
from exporter.adapters.vllm import VllmAdapter
from exporter.backend_state import snapshot_from_derived
from exporter.advisor import build_advice
from exporter.config import Settings
from exporter.derive.compute import compute_derived
from exporter.registry.prom_writer import PromWriter

logger = logging.getLogger(__name__)


def _new_scrape_state() -> dict[str, object]:
    return {
        "last_success_ts": None,
        "consecutive_failures": 0,
        "last_error": "",
    }


def _build_adapter(settings: Settings) -> VllmAdapter | SGLangAdapter:
    backend_type = (settings.backend_type or "vllm").strip().lower()
    if backend_type == "sglang":
        return SGLangAdapter(
            metrics_url=settings.backend_metrics_url,
            model_name=settings.model_name,
            instance_name=settings.instance_name,
            model_group=settings.model_group,
            tokens_per_block=settings.tokens_per_block,
        )
    return VllmAdapter(
        metrics_url=settings.backend_metrics_url,
        model_name=settings.model_name,
        instance_name=settings.instance_name,
        model_group=settings.model_group,
    )


def create_app(settings: Settings | None = None, *, start_poll_loop: bool = True) -> FastAPI:
    settings = settings or Settings()
    writer = PromWriter()
    backend_state: dict[str, object] = {}
    advice: dict[str, object] = {}
    scrape_state = _new_scrape_state()
    adapter = _build_adapter(settings)

    async def poll_loop() -> None:
        while True:
            try:
                native = await adapter.collect()
                derived = compute_derived(native)
                writer.write(derived)
                backend_state.clear()
                backend_state.update(snapshot_from_derived(derived, backend_id=settings.backend_id or None))
                advice.clear()
                advice.update(build_advice(derived))
                advice["observed_at_unix_millis"] = backend_state["observed_at_unix_millis"]
                state_dir = Path(settings.state_dir)
                state_dir.mkdir(parents=True, exist_ok=True)
                (state_dir / "backend-state.json").write_text(json.dumps(backend_state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                with (state_dir / "advice.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(advice, sort_keys=True) + "\n")
                scrape_state["last_success_ts"] = time.time()
                scrape_state["consecutive_failures"] = 0
                scrape_state["last_error"] = ""
                writer.set_scrape_health(
                    last_success_ts=float(scrape_state["last_success_ts"]),
                    consecutive_failures=0,
                )
            except Exception:
                scrape_state["consecutive_failures"] = int(scrape_state["consecutive_failures"]) + 1
                err = traceback.format_exc()
                scrape_state["last_error"] = err[: settings.scrape_error_max_len]
                advice.clear()
                advice.update({"schema_version": "kavora-advice/v1", "status": "unavailable", "reason": "scrape_failed", "detail": scrape_state["last_error"]})
                logger.exception("backend scrape failed")
                writer.inc_scrape_failure()
                writer.set_scrape_health(
                    last_success_ts=scrape_state["last_success_ts"],  # type: ignore[arg-type]
                    consecutive_failures=int(scrape_state["consecutive_failures"]),
                )
            await asyncio.sleep(max(settings.poll_interval_s, 0.2))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        poll_task = None
        if start_poll_loop:
            poll_task = asyncio.create_task(poll_loop())
            app.state.poll_task = poll_task
        try:
            yield
        finally:
            if poll_task:
                poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task

    app = FastAPI(title="KV Cache Exporter", lifespan=lifespan)
    app.state.settings = settings
    app.state.writer = writer
    app.state.scrape_state = scrape_state
    app.state.start_poll_loop = start_poll_loop
    app.state.backend_state = backend_state
    app.state.advice = advice

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", response_model=None)
    async def readyz() -> JSONResponse | dict[str, object]:
        now = time.time()
        last = scrape_state["last_success_ts"]
        fails = int(scrape_state["consecutive_failures"])
        stale = last is None or (now - float(last) > settings.scrape_stale_after_s)
        too_many_failures = fails >= settings.scrape_failures_not_ready
        if stale or too_many_failures:
            body = {
                "ready": False,
                "last_success_ts": last,
                "consecutive_failures": fails,
                "stale": stale,
                "too_many_failures": too_many_failures,
                "last_error": scrape_state["last_error"],
            }
            return JSONResponse(status_code=503, content=body)
        return {"ready": True, "last_success_ts": last, "consecutive_failures": fails}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=writer.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/backend-state")
    async def backend_state_snapshot() -> JSONResponse:
        if not backend_state:
            return JSONResponse(status_code=503, content={"ready": False, "reason": "no_snapshot"})
        return JSONResponse(content=backend_state)

    @app.get("/advice")
    async def tuning_advice() -> JSONResponse:
        if not advice:
            return JSONResponse(status_code=503, content={"status": "unavailable", "reason": "no_snapshot"})
        return JSONResponse(content=advice)

    return app


app = create_app()


def main() -> None:
    settings = app.state.settings
    uvicorn.run(app, host=settings.exporter_host, port=settings.exporter_port)


if __name__ == "__main__":
    main()
