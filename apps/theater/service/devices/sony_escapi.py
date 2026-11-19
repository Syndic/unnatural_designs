"""Sony STR-AZ1000ES / STR-AZ5000ES client.

The receivers expose Sony's ESCAPI — same JSON-RPC-over-HTTP shape as Bravia
TVs, different service catalog. Documentation:
  https://pro-bravia.sony.net/develop/integrate/rest-api/spec/

Endpoints we touch:
  POST /sony/avContent  setPlayContent / getPlayingContentInfo / getSchemeList
  POST /sony/audio      setAudioVolume / setAudioMute / setSoundSettings
  POST /sony/audio      getCurrentExternalTerminalsStatus  (BT devices)
  POST /sony/system     getPowerStatus / setPowerStatus / getInterfaceInformation

Differences from the TV client:
  - Zone-aware: the AZ family has main + zone-2 outputs. Many methods take
    a `zone` parameter or use a zone-suffixed URI ("extOutput:zone?zone=2").
  - The center AZ5000ES has BT headphones + sound fields; the side AZ1000ES
    units are HDMI switchers only.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, Optional

import httpx

log = logging.getLogger(__name__)


ReceiverKind = Literal["az1000", "az5000"]


# Source label → receiver-internal URI. The receiver only knows HDMI port
# numbers; the human-facing names come from the wiring map.
# In real deployment, this is populated from service/config.toml.
SOURCE_URI_FORMAT = "extInput:hdmi?port={port}"


class SonyEscapiReceiver:
    def __init__(self, *, device_id: str, host: str, psk: str,
                 kind: ReceiverKind,
                 source_to_port: dict[str, int]):
        """
        Args:
          source_to_port: maps human source labels ('PS5', 'Xbox') to the
            HDMI port number on this receiver. Loaded from config.
        """
        self.id = device_id
        self.host = host
        self.kind = kind
        self._psk = psk
        self._source_to_port = source_to_port
        self._port_to_source = {p: s for s, p in source_to_port.items()}
        self._client: Optional[httpx.AsyncClient] = None
        self._last_ok: float = 0.0

    # ----- lifecycle ----------------------------------------------------

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"http://{self.host}:10000",
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

    # ----- inputs / zones ----------------------------------------------

    async def set_input(self, zone: int, source: str) -> None:
        port = self._source_to_port.get(source)
        if port is None:
            raise ValueError(f"unknown source {source!r} on {self.id}")
        uri = SOURCE_URI_FORMAT.format(port=port)
        params: dict[str, Any] = {"uri": uri}
        if zone != 1:
            params["output"] = f"extOutput:zone?zone={zone}"
        await self._rpc("avContent", "setPlayContent", params)

    async def current_input(self, zone: int) -> str:
        params = {"output": f"extOutput:zone?zone={zone}"} if zone != 1 else {}
        res = await self._rpc("avContent", "getPlayingContentInfo", params or None)
        uri = res.get("uri", "")
        if "port=" in uri:
            port = int(uri.rsplit("=", 1)[-1])
            return self._port_to_source.get(port, f"HDMI {port}")
        return uri

    # ----- volume / mute / output (center only) ------------------------

    async def set_volume(self, volume: int) -> None:
        await self._rpc("audio", "setAudioVolume", {
            "target": "speaker",  # AZ5000ES uses 'speaker' for main; 'headphone' for BT
            "volume": str(volume),
        })

    async def current_volume(self) -> int:
        # getVolumeInformation returns an array of {target, volume, minVolume, maxVolume}.
        # TODO(claude-code): pick the entry whose target matches the current `out`.
        raise NotImplementedError

    async def set_mute(self, muted: bool) -> None:
        await self._rpc("audio", "setAudioMute", {
            "status": "on" if muted else "off",
        })

    async def set_output(self, out: str) -> None:
        """'speakers' | 'headphones'. Only valid on AZ5000ES."""
        if self.kind != "az5000":
            raise ValueError(f"{self.id}: output routing only on AZ5000ES")
        # AZ5000ES routes via "soundSettings" with target=outputTerminal.
        value = "speaker" if out == "speakers" else "headphone"
        await self._rpc("audio", "setSoundSettings", {
            "settings": [{"target": "outputTerminal", "value": value}],
        })

    async def set_sound_field(self, sf: str) -> None:
        """Only on AZ5000ES."""
        if self.kind != "az5000":
            raise ValueError(f"{self.id}: sound field only on AZ5000ES")
        # Map our short ID to Sony's full value name.
        value = {
            "atmos":  "dolbyAtmos",
            "dtsx":   "dtsX",
            "afd":    "afd",
            "multi":  "multiStereo",
            "stereo": "stereo",
            "direct": "direct",
        }[sf]
        await self._rpc("audio", "setSoundSettings", {
            "settings": [{"target": "soundField", "value": value}],
        })

    # ----- bluetooth ----------------------------------------------------

    async def list_bt_devices(self) -> list[dict]:
        """Returns paired BT devices: [{id, name, battery, status}]."""
        # TODO(claude-code): wire to getCurrentExternalTerminalsStatus filtered
        # to bluetooth peers, plus getBluetoothSettings for the active one.
        raise NotImplementedError

    async def activate_bt_device(self, device_id: str) -> None:
        # TODO(claude-code): setBluetoothSettings target=device value=<id>
        raise NotImplementedError

    async def forget_bt_device(self, device_id: str) -> None:
        # TODO(claude-code): some firmware exposes a delete; if not, log and
        # surface in the frontend that pairing must be cleared at the receiver.
        raise NotImplementedError

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
            raise RuntimeError(f"ESCAPI {service}.{method}: {body['error']}")
        result = body.get("result", [])
        return result[0] if result else {}
