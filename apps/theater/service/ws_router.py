"""WebSocket router.

Each client connection:
  1. Receives the full state once (`{type: "state", state: ...}`).
  2. Receives `{type: "patch", version, ops}` for every state change.
  3. May send `{type: "command", id, endpoint, body}` to invoke commands
     over the same socket — preferred over REST so the resulting patch and
     the ack arrive on the same channel without race.

Disconnect / reconnect is handled by the client. The frontend reconnects
with exponential backoff and re-runs the state envelope on reattach.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .api.schema import (
    API_VERSION, AudioOut, AudioOutCmd, BtDeviceCmd, LightLevelCmd,
    LightSceneCmd, MuteCmd, SoundFieldCmd, StationKey, TvKey, TvPowerCmd,
    TvSourceCmd, VolumeCmd, WsAck, WsPatch, WsPatchOp, WsState,
)
from .state import Room

log = logging.getLogger(__name__)


def make_router(room_provider) -> APIRouter:
    r = APIRouter()

    @r.websocket("/api/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        room: Room = room_provider()

        # Send the initial state envelope.
        await socket.send_json(
            WsState(state=room.state).model_dump(mode="json"))

        # Hook into the room's broadcast.
        queue: asyncio.Queue[tuple[int, list[WsPatchOp]]] = asyncio.Queue(maxsize=256)

        async def push(version: int, ops: list[WsPatchOp]) -> None:
            try:
                queue.put_nowait((version, ops))
            except asyncio.QueueFull:
                log.warning("ws queue full; dropping client")
                await socket.close(code=1011)

        room.subscribe(push)

        # Ping every 30s to keep proxies honest.
        async def pinger():
            while True:
                await asyncio.sleep(30)
                await socket.send_json({"type": "ping", "ts": int(time.time())})

        pinger_task = asyncio.create_task(pinger())

        # Patch forwarder.
        async def forwarder():
            while True:
                version, ops = await queue.get()
                await socket.send_json(
                    WsPatch(version=version, ops=tuple(ops)).model_dump(mode="json"))

        forwarder_task = asyncio.create_task(forwarder())

        try:
            while True:
                msg = await socket.receive_text()
                try:
                    env = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if env.get("type") == "command":
                    await _handle_command(socket, room, env)
                elif env.get("type") == "pong":
                    pass
                else:
                    log.warning("unknown ws envelope: %s", env)
        except WebSocketDisconnect:
            pass
        finally:
            room.unsubscribe(push)
            pinger_task.cancel()
            forwarder_task.cancel()

    return r


async def _handle_command(socket: WebSocket, room: Room, env: dict[str, Any]) -> None:
    """Dispatch a WS command envelope. Mirrors the REST router but pairs the
    ack with the patch on the same socket."""
    cmd_id = env.get("id", "?")
    endpoint: str = env.get("endpoint", "")
    body: dict = env.get("body", {})

    try:
        # Dispatch table — endpoint string → (parser, room method).
        # Keep this tight; the REST router has the canonical list.
        if endpoint.startswith("tv/"):
            _, tv, action = endpoint.split("/", 2)
            tv_key = TvKey(tv)
            if action == "power":
                await room.tv_power(tv_key, TvPowerCmd(**body).power)
            elif action == "source":
                c = TvSourceCmd(**body)
                await room.tv_source(tv_key, c.station, c.source)
            else:
                raise ValueError(f"unknown tv action {action}")

        elif endpoint == "all_off":
            await room.all_off()

        elif endpoint.startswith("audio/"):
            await _handle_audio(room, endpoint, body)

        elif endpoint.startswith("lights/"):
            await _handle_lights(room, endpoint, body)

        else:
            raise ValueError(f"unknown endpoint {endpoint}")

        await socket.send_json(WsAck(id=cmd_id, ok=True).model_dump())
    except Exception as e:
        log.warning("ws command %s failed: %s", endpoint, e)
        await socket.send_json(
            WsAck(id=cmd_id, ok=False, error=str(e)).model_dump())


async def _handle_audio(room: Room, endpoint: str, body: dict) -> None:
    parts = endpoint.split("/")
    # audio/<station>/<action>[/<sub>]
    station = StationKey(parts[1])
    action = parts[2]
    if action == "volume":
        await room.set_volume(station, VolumeCmd(**body).volume)
    elif action == "mute":
        await room.set_mute(station, MuteCmd(**body).muted)
    elif action == "output":
        await room.set_audio_out(station, AudioOutCmd(**body).out)
    elif action == "sound_field":
        await room.set_sound_field(SoundFieldCmd(**body).sound_field)
    elif action == "bt" and len(parts) >= 4:
        sub = parts[3]
        if sub == "activate":
            await room.activate_bt(station, BtDeviceCmd(**body).device_id)
        elif sub == "forget":
            await room.forget_bt(station, BtDeviceCmd(**body).device_id)
        else:
            raise ValueError(f"unknown bt sub {sub}")
    else:
        raise ValueError(f"unknown audio action {action}")


async def _handle_lights(room: Room, endpoint: str, body: dict) -> None:
    parts = endpoint.split("/")
    sub = parts[1]
    if sub == "scene":
        await room.set_scene(LightSceneCmd(**body).scene)
    elif sub == "level":
        await room.set_level(LightLevelCmd(**body).level)
    else:
        raise ValueError(f"unknown lights sub {sub}")
