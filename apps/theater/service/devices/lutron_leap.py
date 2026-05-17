"""Lutron LEAP client.

Speaks the protocol used by Caseta Smart Bridge Pro 2 and RA3 processors.
We use pylutron-caseta (it supports both).

Pairing produces three files under ~/.config/theater/lutron/:
  cert.pem        — our client cert, signed by the processor
  key.pem         — our private key
  ca.pem          — the processor's CA cert

Once paired, this client streams events: any keypad press or dimmer change
elsewhere in the room shows up here, so the UI never drifts from reality.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

# TODO(claude-code): from pylutron_caseta.smartbridge import Smartbridge

log = logging.getLogger(__name__)


class LutronProcessor:
    def __init__(self, *, device_id: str, host: str, cert_dir: Path):
        self.id = device_id
        self.host = host
        self.cert_dir = cert_dir
        self._bridge = None  # Smartbridge
        self._last_ok: float = 0.0

    async def connect(self) -> None:
        # TODO(claude-code):
        # self._bridge = Smartbridge.create_tls(
        #     hostname=self.host,
        #     keyfile=str(self.cert_dir / "key.pem"),
        #     certfile=str(self.cert_dir / "cert.pem"),
        #     ca_certs=str(self.cert_dir / "ca.pem"),
        # )
        # await self._bridge.connect()
        raise NotImplementedError

    async def disconnect(self) -> None:
        # TODO(claude-code): await self._bridge.close()
        pass

    async def probe(self) -> float:
        # Cheapest probe: list devices and time it.
        t0 = time.monotonic()
        # TODO(claude-code): self._bridge.get_devices()
        latency = time.monotonic() - t0
        self._last_ok = time.time()
        return latency

    # ----- commands -----------------------------------------------------

    async def select_scene(self, scene_id: str) -> None:
        # TODO(claude-code): await self._bridge.activate_scene(scene_id)
        raise NotImplementedError

    async def set_level(self, dimmer_id: str, level: int) -> None:
        # level: 0..100. Lutron uses the same scale.
        # TODO(claude-code): await self._bridge.set_value(dimmer_id, level)
        raise NotImplementedError

    async def current_level(self, dimmer_id: str) -> int:
        # TODO(claude-code): dev = self._bridge.get_device_by_id(dimmer_id)
        # return int(dev["current_state"])
        raise NotImplementedError

    # ----- events -------------------------------------------------------

    def add_subscriber(self, dimmer_id: str, callback) -> None:
        """Fire `callback()` whenever the named device changes state on the
        processor. Use this to keep our state mirrored even when a wall
        keypad is pressed."""
        # TODO(claude-code): self._bridge.add_subscriber(dimmer_id, callback)
        raise NotImplementedError
