"""LG webOS TV client. Targets the G4 specifically; should work on any
modern webOS (5.x+).

Wraps `aiowebostv` to fit the `Tv` protocol. Adds:
  - Wake-on-LAN fallback for power-on when the TV is in deep standby.
  - HDMI input label normalization (LG returns 'HDMI_1' style; we want 'HDMI 1').

Pairing is handled by //apps/theater/cmd/pair_devices, which writes the
issued client key to ~/.config/theater/credentials.json keyed by host.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

# TODO(claude-code): import aiowebostv when wiring real I/O
# from aiowebostv import WebOsClient

from ..api.schema import API_VERSION  # noqa: F401  re-export to anchor module
from .base import Tv

log = logging.getLogger(__name__)


class LgWebOsTv:
    """Concrete LG webOS implementation of `Tv`."""

    def __init__(self, *, device_id: str, host: str, mac: Optional[str],
                 client_key: str):
        self.id = device_id
        self.host = host
        self.mac = mac
        self._client_key = client_key
        self._client = None  # WebOsClient
        self._last_ok: float = 0.0

    # ----- lifecycle ----------------------------------------------------

    async def connect(self) -> None:
        """Open the WS to the TV. If the TV is in standby and we have a MAC,
        send a WoL first."""
        # TODO(claude-code): create WebOsClient, wait for it to be connected.
        # On failure, if mac is set, send WoL and retry once with a 5s wait.
        raise NotImplementedError

    async def disconnect(self) -> None:
        # TODO(claude-code): self._client.disconnect()
        pass

    async def probe(self) -> float:
        """Hit a cheap endpoint (system info) and time it."""
        t0 = time.monotonic()
        # TODO(claude-code): await self._client.get_system_info()
        # raise on failure
        latency = time.monotonic() - t0
        self._last_ok = time.time()
        return latency

    # ----- commands -----------------------------------------------------

    async def power(self, on: bool) -> None:
        if on:
            # If WS isn't up, WoL first.
            if self._client is None or not self._connected():
                await self._wol()
                await asyncio.sleep(4)  # give the TV time to attach
                await self.connect()
            # webOS exposes 'power on via post-boot' — usually already on after WoL.
            return
        # off: webOS power-off command. Connection drops; that's expected.
        # TODO(claude-code): await self._client.power_off()
        raise NotImplementedError

    async def select_input(self, hdmi_label: str) -> None:
        """`hdmi_label` is the human label ('HDMI 2'); convert to webOS's
        internal id ('HDMI_2')."""
        # TODO(claude-code): map "HDMI 2" -> "HDMI_2" and call self._client.set_input(...)
        raise NotImplementedError

    async def current_input(self) -> str:
        # TODO(claude-code): query self._client.get_input(), map "HDMI_2" -> "HDMI 2"
        raise NotImplementedError

    # ----- helpers ------------------------------------------------------

    def _connected(self) -> bool:
        # TODO(claude-code): self._client.is_connected()
        return False

    async def _wol(self) -> None:
        """Send a magic packet to the TV's MAC. Broadcast on the LAN — requires
        container host networking when running in Docker."""
        if not self.mac:
            log.warning("No MAC for %s; cannot WoL", self.id)
            return
        # TODO(claude-code): import wakeonlan; wakeonlan.send_magic_packet(self.mac)
        log.info("WoL → %s (%s)", self.id, self.mac)
