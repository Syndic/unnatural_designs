"""Sony Bravia TV client (A1E / OLED75A1E and contemporaries).

Bravia IP Control is a JSON-RPC-over-HTTP API with a pre-shared key in the
'X-Auth-PSK' header. Documented at:
  https://pro-bravia.sony.net/develop/integrate/rest-api/spec/

The relevant endpoints:
  POST /sony/system    method=getInterfaceInformation     (probe)
  POST /sony/system    method=setPowerStatus
  POST /sony/avContent method=setPlayContent
  POST /sony/avContent method=getPlayingContentInfo

Wake-on-LAN: Sony's IP Control accepts power-on commands while the TV is in
Network Standby. WoL is the fallback for full standby.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from .base import Tv

log = logging.getLogger(__name__)


class SonyBraviaTv:
    def __init__(self, *, device_id: str, host: str, mac: Optional[str],
                 psk: str):
        self.id = device_id
        self.host = host
        self.mac = mac
        self._psk = psk
        self._client: Optional[httpx.AsyncClient] = None
        self._last_ok: float = 0.0

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"http://{self.host}",
            headers={"X-Auth-PSK": self._psk},
            timeout=4.0,
        )
        await self.probe()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> float:
        t0 = time.monotonic()
        await self._rpc("system", "getInterfaceInformation")
        latency = time.monotonic() - t0
        self._last_ok = time.time()
        return latency

    async def power(self, on: bool) -> None:
        if on and self.mac:
            # Best-effort WoL — harmless if already on.
            await self._wol()
            await asyncio.sleep(2)
        await self._rpc("system", "setPowerStatus", {"status": on})

    async def select_input(self, hdmi_label: str) -> None:
        # Bravia URIs look like "extInput:hdmi?port=2" — convert from "HDMI 2".
        port = hdmi_label.removeprefix("HDMI").strip()
        uri = f"extInput:hdmi?port={port}"
        await self._rpc("avContent", "setPlayContent", {"uri": uri})

    async def current_input(self) -> str:
        res = await self._rpc("avContent", "getPlayingContentInfo")
        uri: str = res.get("uri", "")
        # uri is "extInput:hdmi?port=2" → "HDMI 2"
        if "hdmi?port=" in uri:
            return f"HDMI {uri.rsplit('=', 1)[-1]}"
        return uri  # caller will log if unrecognized

    # ----- internals ----------------------------------------------------

    async def _rpc(self, service: str, method: str,
                   params: Optional[dict[str, Any]] = None,
                   *, version: str = "1.0") -> dict:
        if self._client is None:
            raise RuntimeError("not connected")
        payload = {
            "method": method,
            "id": 1,
            "params": [params] if params else [],
            "version": version,
        }
        resp = await self._client.post(f"/sony/{service}", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Bravia {service}.{method} error: {body['error']}")
        result = body.get("result", [])
        return result[0] if result else {}

    async def _wol(self) -> None:
        if not self.mac:
            return
        # TODO(claude-code): wakeonlan.send_magic_packet(self.mac)
        log.info("WoL → %s (%s)", self.id, self.mac)
