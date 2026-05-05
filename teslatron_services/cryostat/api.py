from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .config import CryostatServiceConfig, load_config
from .service import CryostatService


class RampTemperatureRequest(BaseModel):
    target_K: Annotated[float, Field(ge=0)]
    rate_K_per_min: Annotated[float, Field(gt=0)]


class RampFieldRequest(BaseModel):
    target_T: float
    rate_T_per_min: Annotated[float, Field(gt=0)]


def create_app(config: CryostatServiceConfig | None = None) -> FastAPI:
    service = CryostatService(config or load_config())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await service.start()
        app.state.cryostat = service
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Teslatron Cryostat Service", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/config")
    async def get_config() -> dict:
        return service.config_snapshot()

    @app.get("/state")
    async def get_state() -> dict:
        return service.state.to_dict()

    @app.get("/diagnostics")
    async def diagnostics() -> dict:
        return service.diagnostics()

    @app.get("/diagnostics/resources")
    async def diagnostics_resources() -> dict:
        return service.visa_resources()

    @app.get("/diagnostics/catalog")
    async def diagnostics_catalog() -> dict:
        try:
            return service.catalog()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/diagnostics/readings")
    async def diagnostics_readings() -> dict:
        try:
            return service.raw_readings()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/commands/ramp-temperature")
    async def ramp_temperature(request: RampTemperatureRequest) -> dict:
        try:
            return await service.ramp_temperature(
                request.target_K,
                request.rate_K_per_min,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/commands/ramp-field")
    async def ramp_field(request: RampFieldRequest) -> dict:
        try:
            return await service.ramp_field(
                request.target_T,
                request.rate_T_per_min,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/commands/hold")
    async def hold() -> dict:
        try:
            return await service.hold()
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/commands/abort")
    async def abort() -> dict:
        try:
            return await service.abort()
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.websocket("/ws/state")
    async def websocket_state(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = await service.subscribe()
        try:
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            service.unsubscribe(queue)

    return app


app = create_app()
