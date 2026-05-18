from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import ElectricalServiceConfig, load_config
from .orchestrator import ElectricalMeasurementService


class PeriodicRunRequest(BaseModel):
    run_id: str = "electrical_run"
    instrument: str
    interval_s: float = Field(gt=0)
    max_points: int | None = Field(default=None, gt=0)
    plan_id: str = "periodic"
    require_safe_to_measure: bool = True


class RecipeSignalRequest(BaseModel):
    signal: str
    message: str | None = None


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _model_payload(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def create_app(
    config: ElectricalServiceConfig | None = None,
    service_factory: Callable[[ElectricalServiceConfig], ElectricalMeasurementService]
    | None = None,
) -> FastAPI:
    service_config = config or load_config(_default_config_path())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        factory = service_factory or (lambda cfg: ElectricalMeasurementService(cfg))
        service = factory(service_config)
        await service.start()
        app.state.electrical = service
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Teslatron Q-MAT Electrical Measurement Service", lifespan=lifespan)
    app.add_exception_handler(ValueError, value_error_handler)

    def service() -> ElectricalMeasurementService:
        return app.state.electrical

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/config")
    async def get_config() -> dict:
        current_service = getattr(app.state, "electrical", None)
        if current_service is None:
            return service_config.to_dict()
        return current_service.config_snapshot()

    @app.get("/state")
    async def get_state() -> dict:
        return service().state_snapshot()

    @app.get("/runs")
    async def get_runs() -> dict:
        return {"run": service().run_status()}

    @app.get("/plans")
    async def get_plans() -> dict:
        return {"plans": service().list_plans()}

    @app.post("/runs/start")
    async def start_run(request: PeriodicRunRequest) -> dict:
        return await service().start_periodic_run(
            run_id=request.run_id,
            instrument=request.instrument,
            interval_s=request.interval_s,
            max_points=request.max_points,
            plan_id=request.plan_id,
            require_safe_to_measure=request.require_safe_to_measure,
        )

    @app.post("/runs/stop")
    async def stop_run() -> dict:
        return await service().stop_run()

    @app.post("/plans/recipe-signal")
    async def recipe_signal(request: RecipeSignalRequest) -> dict:
        payload = _model_payload(request)
        return await service().trigger_recipe_signal(payload["signal"], payload.get("message"))

    @app.get("/results/latest")
    async def latest_result() -> dict:
        return {"event": service().state_snapshot()["run"]["last_event"]}

    return app


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "electrical.mock.example.json"


app = create_app()
