"""Room state engine.

Holds the canonical TheaterState. Every command goes through `Room.apply()`,
which fans out to the relevant device clients, then atomically updates the
in-memory state and broadcasts a JSON Patch to every WebSocket subscriber.

Two invariants this enforces:
  1. State is only mutated inside `apply()` while holding `_lock`.
  2. State.version increments on every mutation; patches always reference
     the new version. The frontend resyncs if a patch arrives with an
     unexpected version.

A background probe task refreshes `connections` every PROBE_INTERVAL seconds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .api.schema import (
    AudioOut, AudioState, BtDevice, BtDeviceStatus, ConnectionState,
    ConnectionStatus, LightScene, LightsState, SoundField, StationKey,
    TheaterState, TvKey, TvState, WsPatchOp,
)
from .api.wiring import STATIONS, TVS, resolve_tv_hdmi

log = logging.getLogger(__name__)

PROBE_INTERVAL_S = 15.0


PatchListener = Callable[[int, list[WsPatchOp]], Awaitable[None]]
"""Callback signature for WS subscribers: (new_version, ops) -> None."""


class Room:
    """The live theater. Owns state + the device clients. Async-safe."""

    def __init__(self, devices: dict[str, Any], initial: TheaterState):
        self._devices = devices
        self._state = initial
        self._lock = asyncio.Lock()
        self._listeners: set[PatchListener] = set()
        self._probe_task: asyncio.Task | None = None

    # ----- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        # Connect every device. Failures don't abort startup — they just mark
        # the device offline in connections.
        for dev_id, dev in self._devices.items():
            try:
                await dev.connect()
            except Exception as e:
                log.warning("device %s failed to connect: %s", dev_id, e)
                await self._mark_offline(dev_id)
        self._probe_task = asyncio.create_task(self._probe_loop())

    async def stop(self) -> None:
        if self._probe_task:
            self._probe_task.cancel()
        for dev in self._devices.values():
            try:
                await dev.disconnect()
            except Exception:
                pass

    # ----- listener registration ---------------------------------------

    def subscribe(self, fn: PatchListener) -> None:
        self._listeners.add(fn)

    def unsubscribe(self, fn: PatchListener) -> None:
        self._listeners.discard(fn)

    @property
    def state(self) -> TheaterState:
        return self._state

    # ----- public commands ---------------------------------------------

    async def tv_power(self, tv: TvKey, on: bool) -> None:
        dev = self._devices[f"tv_{tv.value}"]
        await dev.power(on)
        await self._patch([
            WsPatchOp(op="replace", path=f"/tvs/{tv.value}/power", value=on),
        ])

    async def tv_source(self, tv: TvKey, station: StationKey, source: str) -> None:
        # 1. Validate the source exists on that station.
        if source not in STATIONS[station].sources:
            raise ValueError(f"unknown source {source!r} on {station.value}")
        # 2. Find the right HDMI input on the TV.
        inp = resolve_tv_hdmi(tv, station)
        # 3. Tell the receiver to send `source` to that zone.
        rcvr = self._devices[f"rcvr_{station.value}"]
        await rcvr.set_input(zone=inp.zone, source=source)
        # 4. Tell the TV to select that HDMI input.
        tv_dev = self._devices[f"tv_{tv.value}"]
        await tv_dev.select_input(inp.label)
        # 5. State update + broadcast.
        ops = [
            WsPatchOp(op="replace", path=f"/tvs/{tv.value}/station", value=station.value),
            WsPatchOp(op="replace", path=f"/tvs/{tv.value}/source", value=source),
            WsPatchOp(op="replace", path=f"/tvs/{tv.value}/power", value=True),
        ]
        await self._patch(ops)

    async def all_off(self) -> None:
        """Power off the three TVs. Receivers stay in network standby; lighting
        is intentionally untouched."""
        await asyncio.gather(
            *(self._devices[f"tv_{k.value}"].power(False) for k in TvKey),
            return_exceptions=True,
        )
        ops = [WsPatchOp(op="replace", path=f"/tvs/{k.value}/power", value=False)
               for k in TvKey]
        await self._patch(ops)

    async def set_volume(self, station: StationKey, volume: int) -> None:
        if station != StationKey.CENTER:
            raise ValueError("volume only on center receiver")
        rcvr = self._devices[f"rcvr_{station.value}"]
        await rcvr.set_volume(volume)
        await self._patch([WsPatchOp(
            op="replace", path=f"/audio/{station.value}/volume", value=volume)])

    async def set_mute(self, station: StationKey, muted: bool) -> None:
        rcvr = self._devices[f"rcvr_{station.value}"]
        await rcvr.set_mute(muted)
        await self._patch([WsPatchOp(
            op="replace", path=f"/audio/{station.value}/muted", value=muted)])

    async def set_audio_out(self, station: StationKey, out: AudioOut) -> None:
        rcvr = self._devices[f"rcvr_{station.value}"]
        await rcvr.set_output(out.value)
        await self._patch([WsPatchOp(
            op="replace", path=f"/audio/{station.value}/out", value=out.value)])

    async def set_sound_field(self, sf: SoundField) -> None:
        rcvr = self._devices["rcvr_center"]
        await rcvr.set_sound_field(sf.value)
        await self._patch([WsPatchOp(
            op="replace", path="/audio/center/sound_field", value=sf.value)])

    async def activate_bt(self, station: StationKey, device_id: str) -> None:
        rcvr = self._devices[f"rcvr_{station.value}"]
        await rcvr.activate_bt_device(device_id)
        await self._patch([
            WsPatchOp(op="replace", path=f"/audio/{station.value}/active", value=device_id),
        ])

    async def forget_bt(self, station: StationKey, device_id: str) -> None:
        rcvr = self._devices[f"rcvr_{station.value}"]
        await rcvr.forget_bt_device(device_id)
        # Update the paired_devices array by re-fetching.
        devs = await rcvr.list_bt_devices()
        await self._patch([
            WsPatchOp(op="replace", path=f"/audio/{station.value}/paired_devices", value=devs),
        ])

    async def set_scene(self, scene: LightScene) -> None:
        # Real scene IDs come from config — for now the scene enum doubles as ID.
        lutron = self._devices["lutron"]
        # TODO(claude-code): map scene enum -> config-loaded Lutron scene id.
        await lutron.select_scene(scene.value)
        # Look up the canonical level for this scene from config.
        level = {LightScene.ALLON: 100, LightScene.INTERMISSION: 40,
                 LightScene.MOVIE: 4, LightScene.OFF: 0}.get(scene, self._state.lights.level)
        await self._patch([
            WsPatchOp(op="replace", path="/lights/scene", value=scene.value),
            WsPatchOp(op="replace", path="/lights/level", value=level),
        ])

    async def set_level(self, level: int) -> None:
        lutron = self._devices["lutron"]
        # TODO(claude-code): load room-dimmer id from config.
        await lutron.set_level("device/room", level)
        await self._patch([
            WsPatchOp(op="replace", path="/lights/level", value=level),
            WsPatchOp(op="replace", path="/lights/scene", value="custom"),
        ])

    # ----- internals ----------------------------------------------------

    async def _patch(self, ops: list[WsPatchOp]) -> None:
        """Atomically apply patch ops to state, bump version, broadcast."""
        async with self._lock:
            new = _apply_patch(self._state, ops)
            new_state = new.model_copy(update={"version": new.version + 1})
            self._state = new_state
        await self._broadcast(new_state.version, ops)

    async def _broadcast(self, version: int, ops: list[WsPatchOp]) -> None:
        # Snapshot listeners so a slow callback can't block mutations.
        listeners = list(self._listeners)
        await asyncio.gather(
            *(fn(version, ops) for fn in listeners),
            return_exceptions=True,
        )

    async def _mark_offline(self, device_id: str) -> None:
        await self._patch([WsPatchOp(
            op="replace",
            path=f"/connections/{device_id}",
            value={"status": "offline", "latency_ms": None, "last_ok_s": None},
        )])

    async def _probe_loop(self) -> None:
        while True:
            for dev_id, dev in self._devices.items():
                t0 = time.monotonic()
                try:
                    latency = await dev.probe()
                    cs = ConnectionState(
                        status=ConnectionStatus.CONNECTED,
                        latency_ms=int(latency * 1000),
                        last_ok_s=0,
                    )
                except Exception:
                    cs = ConnectionState(
                        status=ConnectionStatus.OFFLINE,
                        latency_ms=None,
                        last_ok_s=int(time.time() - getattr(dev, "_last_ok", 0)),
                    )
                await self._patch([WsPatchOp(
                    op="replace", path=f"/connections/{dev_id}",
                    value=cs.model_dump(),
                )])
            await asyncio.sleep(PROBE_INTERVAL_S)


def _apply_patch(state: TheaterState, ops: list[WsPatchOp]) -> TheaterState:
    """Apply RFC-6902-ish ops to a Pydantic state. Used to keep state and the
    broadcast payload in lockstep."""
    # The state model is frozen; round-trip through dict to mutate.
    data = state.model_dump()
    for op in ops:
        _set_path(data, op.path, op.value, op.op)
    return TheaterState.model_validate(data)


def _set_path(root: dict, path: str, value, op: str) -> None:
    parts = [p for p in path.split("/") if p]
    cur = root
    for p in parts[:-1]:
        cur = cur[p]
    if op == "remove":
        cur.pop(parts[-1], None)
    else:
        cur[parts[-1]] = value


def initial_state() -> TheaterState:
    """Seed state used when no device has been probed yet."""
    from .api.schema import API_VERSION  # noqa
    return TheaterState(
        version=0,
        tvs={
            TvKey.LEFT:   TvState(power=False, station=StationKey.LEFT,   source="PS5",         model=TVS[TvKey.LEFT].model),
            TvKey.CENTER: TvState(power=False, station=StationKey.CENTER, source="Handheld PC", model=TVS[TvKey.CENTER].model),
            TvKey.RIGHT:  TvState(power=False, station=StationKey.RIGHT,  source="ViewPort",    model=TVS[TvKey.RIGHT].model),
        },
        audio={
            StationKey.LEFT:   AudioState(out=AudioOut.HEADPHONES, volume=60, paired_devices=()),
            StationKey.CENTER: AudioState(out=AudioOut.SPEAKERS,   volume=50, sound_field=SoundField.ATMOS, paired_devices=()),
            StationKey.RIGHT:  AudioState(out=AudioOut.HEADPHONES, volume=55, paired_devices=()),
        },
        lights=LightsState(),
        connections={
            "tv_left":     ConnectionState(status=ConnectionStatus.OFFLINE),
            "tv_center":   ConnectionState(status=ConnectionStatus.OFFLINE),
            "tv_right":    ConnectionState(status=ConnectionStatus.OFFLINE),
            "rcvr_left":   ConnectionState(status=ConnectionStatus.OFFLINE),
            "rcvr_center": ConnectionState(status=ConnectionStatus.OFFLINE),
            "rcvr_right":  ConnectionState(status=ConnectionStatus.OFFLINE),
            "lutron":      ConnectionState(status=ConnectionStatus.OFFLINE),
        },
    )
