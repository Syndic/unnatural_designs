"""Service entrypoint.

Builds the FastAPI app, wires in REST + WS routes, mounts the compiled
frontend at /, and starts the Room engine on app startup.

Run for dev:
  bazel run //apps/theater/service:dev

Run for prod (in the container):
  uvicorn apps.theater.service.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import rest, ws_router
from .devices._mock import make_mock_devices
from .state import Room, initial_state

log = logging.getLogger(__name__)


def _build_devices(real: bool) -> dict:
    """Real device wiring lives here. Until each module is finished the
    real path raises; mock is the safe default."""
    if not real:
        return make_mock_devices()

    # TODO(claude-code): load config.toml, construct LgWebOsTv / SonyBraviaTv /
    # SonyEscapiReceiver / LutronProcessor instances with credentials from
    # ~/.config/theater/credentials.json.
    raise NotImplementedError("real device wiring not yet built")


_room: Room | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _room
    real = os.getenv("THEATER_REAL_DEVICES", "0") == "1"
    devices = _build_devices(real)
    _room = Room(devices, initial_state())
    await _room.start()
    try:
        yield
    finally:
        await _room.stop()


def _get_room() -> Room:
    assert _room is not None, "Room not started"
    return _room


def create_app() -> FastAPI:
    app = FastAPI(title="Theater Controls", lifespan=lifespan)
    app.include_router(rest.make_router(_get_room))
    app.include_router(ws_router.make_router(_get_room))

    # Mount the compiled frontend at /. Built artifacts land in frontend/dist/
    # via the Bazel frontend target; in dev `bazel run :dev` symlinks the raw
    # source tree to the same path.
    frontend_dir = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="ui")
    else:
        log.warning("frontend not built; UI will 404. Run the frontend target.")

    return app


app = create_app()


# ----- dev runner (bazel run :dev) ------------------------------------------

def main():
    """Dev entry: `python -m apps.theater.service.main --real-devices`."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-devices", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.real_devices:
        os.environ["THEATER_REAL_DEVICES"] = "1"

    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "apps.theater.service.main:app",
        host="0.0.0.0",
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
