"""In-memory mock devices for tests and frontend bridge development.

`make_mock_room()` returns a RoomState wired to mocks that record every call
and return synthetic but plausible responses. Drop-in replacement for the
real device set so the frontend can be developed against this service with
no hardware present.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _CallLog:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def record(self, name: str, **kwargs):
        self.calls.append((name, kwargs))


class MockTv:
    def __init__(self, device_id: str):
        self.id = device_id
        self.log = _CallLog()
        self._on = False
        self._input = "HDMI 1"
        self._last_ok = time.time()

    async def connect(self): self.log.record("connect")
    async def disconnect(self): self.log.record("disconnect")
    async def probe(self) -> float: self._last_ok = time.time(); return 0.02
    async def power(self, on: bool):
        self.log.record("power", on=on); self._on = on
        await asyncio.sleep(0.05)
    async def select_input(self, hdmi_label: str):
        self.log.record("select_input", hdmi_label=hdmi_label); self._input = hdmi_label
    async def current_input(self) -> str: return self._input


class MockReceiver:
    def __init__(self, device_id: str, kind: str = "az1000"):
        self.id = device_id
        self.kind = kind
        self.log = _CallLog()
        self._inputs = {1: "PS5", 2: "Switch"}
        self._volume = 50
        self._muted = False
        self._out = "headphones" if kind == "az1000" else "speakers"
        self._sound_field = "atmos"
        self._bt = [
            {"id": f"{device_id}-hp", "name": "Mock Headphones",
             "battery": 80, "status": "connected"},
        ]
        self._active_bt = self._bt[0]["id"]
        self._last_ok = time.time()

    async def connect(self): self.log.record("connect")
    async def disconnect(self): self.log.record("disconnect")
    async def probe(self) -> float: self._last_ok = time.time(); return 0.04
    async def set_input(self, zone: int, source: str):
        self.log.record("set_input", zone=zone, source=source)
        self._inputs[zone] = source
    async def current_input(self, zone: int) -> str: return self._inputs.get(zone, "Switch")
    async def set_volume(self, volume: int):
        self.log.record("set_volume", volume=volume); self._volume = volume
    async def current_volume(self) -> int: return self._volume
    async def set_mute(self, muted: bool):
        self.log.record("set_mute", muted=muted); self._muted = muted
    async def set_output(self, out: str):
        self.log.record("set_output", out=out); self._out = out
    async def set_sound_field(self, sf: str):
        self.log.record("set_sound_field", sf=sf); self._sound_field = sf
    async def list_bt_devices(self) -> list[dict]: return list(self._bt)
    async def activate_bt_device(self, device_id: str):
        self.log.record("activate_bt_device", device_id=device_id)
        self._active_bt = device_id
    async def forget_bt_device(self, device_id: str):
        self.log.record("forget_bt_device", device_id=device_id)
        self._bt = [d for d in self._bt if d["id"] != device_id]


class MockLutron:
    def __init__(self, device_id: str = "lutron"):
        self.id = device_id
        self.log = _CallLog()
        self._scene = "intermission"
        self._level = 40
        self._last_ok = time.time()

    async def connect(self): self.log.record("connect")
    async def disconnect(self): self.log.record("disconnect")
    async def probe(self) -> float: self._last_ok = time.time(); return 0.01
    async def select_scene(self, scene_id: str):
        self.log.record("select_scene", scene_id=scene_id); self._scene = scene_id
    async def set_level(self, dimmer_id: str, level: int):
        self.log.record("set_level", dimmer_id=dimmer_id, level=level); self._level = level
    async def current_level(self, dimmer_id: str) -> int: return self._level
    def add_subscriber(self, dimmer_id: str, callback): pass


def make_mock_devices() -> dict[str, Any]:
    """Build the full set of mock devices keyed by Theater Controls device IDs."""
    return {
        "tv_left":     MockTv("tv_left"),
        "tv_center":   MockTv("tv_center"),
        "tv_right":    MockTv("tv_right"),
        "rcvr_left":   MockReceiver("rcvr_left",   kind="az1000"),
        "rcvr_center": MockReceiver("rcvr_center", kind="az5000"),
        "rcvr_right":  MockReceiver("rcvr_right",  kind="az1000"),
        "lutron":      MockLutron(),
    }
