"""Per-device async client interfaces.

Every device class implements `Device` so the room state engine can drive
them uniformly. Real I/O is in concrete subclasses; tests use _mock.py.
"""

from __future__ import annotations

import abc
from typing import Protocol


class Device(Protocol):
    """Common surface for every controllable thing in the room."""

    id: str
    """Stable key — matches keys in TheaterState.connections."""

    async def connect(self) -> None:
        """Bring the connection up. Idempotent. Raises if the device is
        unreachable; the state engine catches and marks offline."""
        ...

    async def disconnect(self) -> None:
        """Tear down. Idempotent."""
        ...

    async def probe(self) -> float:
        """Run a cheap reachability check. Returns latency in seconds.
        Raises if the device is offline."""
        ...


class Tv(Device, Protocol):
    """webOS or Bravia TV — same surface."""

    async def power(self, on: bool) -> None: ...
    async def select_input(self, hdmi_label: str) -> None: ...
    async def current_input(self) -> str: ...


class Receiver(Device, Protocol):
    """Sony STR-AZ family. Zone-aware."""

    async def set_input(self, zone: int, source: str) -> None: ...
    async def current_input(self, zone: int) -> str: ...
    async def set_volume(self, volume: int) -> None: ...
    async def current_volume(self) -> int: ...
    async def set_mute(self, muted: bool) -> None: ...
    async def set_output(self, out: str) -> None:
        """'speakers' | 'headphones'. Only valid on the center receiver."""
    async def set_sound_field(self, sf: str) -> None:
        """Only valid on the center receiver."""
    async def list_bt_devices(self) -> list[dict]: ...
    async def activate_bt_device(self, device_id: str) -> None: ...
    async def forget_bt_device(self, device_id: str) -> None: ...


class Lutron(Device, Protocol):
    """RA3 processor."""

    async def select_scene(self, scene_id: str) -> None: ...
    async def set_level(self, dimmer_id: str, level: int) -> None: ...
    async def current_level(self, dimmer_id: str) -> int: ...
