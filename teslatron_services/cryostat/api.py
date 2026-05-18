from __future__ import annotations

import os
import signal
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Callable

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import CryostatServiceConfig, load_config
from .service import CryostatService


class RampTemperatureRequest(BaseModel):
    target_K: Annotated[float, Field(ge=0)]
    rate_K_per_min: Annotated[float, Field(gt=0)]


class TargetTemperatureRequest(BaseModel):
    target_K: Annotated[float, Field(ge=0)]


class RampFieldRequest(BaseModel):
    target_T: float
    rate_T_per_min: Annotated[float, Field(gt=0)]


class RampToZeroRequest(BaseModel):
    rate_T_per_min: Annotated[float, Field(gt=0)]


class NeedleValveRequest(BaseModel):
    needle_valve_percent: Annotated[float, Field(ge=0, le=100)]


class PressureRequest(BaseModel):
    pressure_mbar: Annotated[float, Field(ge=0)]


class FixedHeaterRequest(BaseModel):
    heater_percent: Annotated[float, Field(ge=0, le=100)]


class PIDRequest(BaseModel):
    p: Annotated[float, Field(ge=0)]
    i: Annotated[float, Field(ge=0)]
    d: Annotated[float, Field(ge=0)]
    auto: bool = False


class DiagnosticQueryRequest(BaseModel):
    target: str
    command: str


class SwitchHeaterRequest(BaseModel):
    enabled: bool


class ActivateInsertRequest(BaseModel):
    profile_id: str


class ApplySampleSensorRequest(BaseModel):
    preset_id: str


class RecipeRequest(BaseModel):
    name: str = "Recipe"
    steps: list[dict]
    overwrite: bool = False


class RecipeSignalRequest(BaseModel):
    signal: str
    message: str | None = None


class RecipeRenameRequest(BaseModel):
    new_name: str


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


async def permission_error_handler(
    request: Request, exc: PermissionError
) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


def _model_payload(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _schedule_process_shutdown(delay_s: float = 0.2) -> None:
    def _shutdown() -> None:
        os.kill(os.getpid(), signal.SIGINT)

    timer = threading.Timer(delay_s, _shutdown)
    timer.daemon = True
    timer.start()


def create_app(
    config: CryostatServiceConfig | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> FastAPI:
    service_config = config or load_config(_default_config_path())
    static_dir = Path(__file__).with_name("static")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = CryostatService(service_config)
        await service.start()
        app.state.cryostat = service
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Teslatron Q-MAT Cryostat Service", lifespan=lifespan)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(PermissionError, permission_error_handler)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def service() -> CryostatService:
        return app.state.cryostat

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    if service_config.enable_shutdown:
        @app.post("/shutdown")
        async def shutdown() -> dict[str, str]:
            if shutdown_callback is not None:
                shutdown_callback()
            else:
                _schedule_process_shutdown()
            return {"status": "shutting_down"}

    @app.get("/config")
    async def get_config() -> dict:
        current_service = getattr(app.state, "cryostat", None)
        if current_service is None:
            return service_config.to_dict()
        return current_service.config_snapshot()

    @app.post("/config/activate-insert")
    async def activate_insert(request: ActivateInsertRequest) -> dict:
        return await service().activate_insert_profile(request.profile_id)

    @app.post("/config/apply-sample-sensor")
    async def apply_sample_sensor(request: ApplySampleSensorRequest) -> dict:
        return await service().apply_sample_sensor(request.preset_id)

    @app.get("/state")
    async def get_state() -> dict:
        return service().state_snapshot()

    @app.get("/recipes/status")
    async def recipe_status() -> dict:
        return service().recipe_status()

    @app.get("/recipes")
    async def saved_recipes() -> dict:
        return {"recipes": service().list_saved_recipes()}

    @app.get("/recipes/{recipe_id}")
    async def load_recipe(recipe_id: str) -> dict:
        return service().load_saved_recipe(recipe_id)

    @app.delete("/recipes/{recipe_id}")
    async def delete_recipe(recipe_id: str) -> dict:
        await service().delete_saved_recipe(recipe_id)
        return {"deleted": recipe_id}

    @app.post("/recipes/{recipe_id}/rename")
    async def rename_recipe(recipe_id: str, request: RecipeRenameRequest) -> dict:
        return await service().rename_saved_recipe(recipe_id, request.new_name)

    @app.post("/recipes/{recipe_id}/duplicate")
    async def duplicate_recipe(recipe_id: str, request: RecipeRenameRequest) -> dict:
        return await service().duplicate_saved_recipe(recipe_id, request.new_name)

    @app.post("/recipes/start")
    async def start_recipe(request: RecipeRequest) -> dict:
        return await service().start_recipe(_model_payload(request))

    @app.post("/recipes/save")
    async def save_recipe(request: RecipeRequest) -> dict:
        payload = _model_payload(request)
        overwrite = bool(payload.pop("overwrite", False))
        return await service().save_recipe(payload, overwrite=overwrite)

    @app.post("/recipes/acknowledge")
    async def acknowledge_recipe() -> dict:
        return await service().acknowledge_recipe()

    @app.post("/recipes/signal")
    async def signal_recipe(request: RecipeSignalRequest) -> dict:
        return await service().signal_recipe(request.signal, request.message)

    @app.post("/recipes/abort")
    async def abort_recipe() -> dict:
        return await service().abort_recipe()

    @app.get("/diagnostics")
    async def diagnostics() -> dict:
        return service().diagnostics()

    @app.get("/diagnostics/resources")
    async def diagnostics_resources() -> dict:
        return await service().visa_resources()

    @app.get("/diagnostics/catalog")
    async def diagnostics_catalog() -> dict:
        try:
            return await service().catalog()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/diagnostics/readings")
    async def diagnostics_readings() -> dict:
        try:
            return await service().raw_readings()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/diagnostics/query")
    async def diagnostics_query(request: DiagnosticQueryRequest) -> dict:
        try:
            return await service().diagnostic_query(request.target, request.command)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/commands/ramp-temperature")
    async def ramp_temperature(request: RampTemperatureRequest) -> dict:
        return await service().ramp_temperature(
            request.target_K,
            request.rate_K_per_min,
            loop="both",
        )

    @app.post("/commands/temperature/{loop}/ramp")
    async def ramp_temperature_loop(loop: str, request: RampTemperatureRequest) -> dict:
        return await service().ramp_temperature(
            request.target_K,
            request.rate_K_per_min,
            loop=loop,
        )

    @app.post("/commands/temperature/{loop}/target")
    async def set_temperature_target_loop(
        loop: str,
        request: TargetTemperatureRequest,
    ) -> dict:
        return await service().set_temperature_target(
            request.target_K,
            loop=loop,
        )

    @app.post("/commands/temperature/{loop}/fixed-heater")
    async def fixed_heater_loop(loop: str, request: FixedHeaterRequest) -> dict:
        return await service().set_temperature_fixed_heater(
            loop,
            request.heater_percent,
        )

    @app.post("/commands/temperature/{loop}/pid")
    async def set_pid_loop(loop: str, request: PIDRequest) -> dict:
        return await service().set_temperature_pid(
            loop,
            request.p,
            request.i,
            request.d,
            auto=request.auto,
        )

    @app.post("/commands/ramp-field")
    async def ramp_field(request: RampFieldRequest) -> dict:
        return await service().ramp_field(
            request.target_T,
            request.rate_T_per_min,
        )

    @app.post("/commands/ramp-to-zero")
    async def ramp_to_zero(request: RampToZeroRequest) -> dict:
        return await service().ramp_to_zero(
            request.rate_T_per_min,
        )

    @app.post("/commands/clamp")
    async def clamp() -> dict:
        return await service().clamp()

    @app.post("/commands/hold")
    async def hold() -> dict:
        return await service().hold()

    @app.post("/commands/abort")
    async def abort() -> dict:
        return await service().abort()

    @app.post("/commands/vti/gas/set-needle")
    async def set_vti_needle(request: NeedleValveRequest) -> dict:
        return await service().set_vti_needle(request.needle_valve_percent)

    @app.post("/commands/vti/gas/set-pressure")
    async def set_vti_pressure(request: PressureRequest) -> dict:
        return await service().set_vti_pressure(request.pressure_mbar)

    @app.post("/commands/ips/switch-heater")
    async def set_switch_heater(request: SwitchHeaterRequest) -> dict:
        return await service().set_switch_heater(request.enabled)

    @app.websocket("/ws/state")
    async def websocket_state(websocket: WebSocket) -> None:
        await websocket.accept()
        current_service = service()
        queue = await current_service.subscribe()
        try:
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            current_service.unsubscribe(queue)

    return app


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "cryostat_lab_readonly.json"


app = create_app()
