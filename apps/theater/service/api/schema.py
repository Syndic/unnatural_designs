"""Theater Controls API schema.

Single source of truth for the wire format. Mirror this in the frontend by
regenerating `frontend/types/state.ts` via the script in
//apps/theater/handoff/frontend-bridge.md.

Shapes are kept identical to the design prototype's `useTheater()` state, so
the frontend swap is a transport change, not a model change.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Bump on any breaking change. The WS handshake includes this and the frontend
# refuses to connect on mismatch.
API_VERSION = 1


# ---------------------------------------------------------------- enums ------

class StationKey(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TvKey(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class AudioOut(str, Enum):
    SPEAKERS = "speakers"
    HEADPHONES = "headphones"


class SoundField(str, Enum):
    ATMOS = "atmos"
    DTSX = "dtsx"
    AFD = "afd"
    MULTI = "multi"
    STEREO = "stereo"
    DIRECT = "direct"


class LightScene(str, Enum):
    ALLON = "allon"
    INTERMISSION = "intermission"
    MOVIE = "movie"
    OFF = "off"
    CUSTOM = "custom"  # set when the dimmer is moved off a preset


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class BtDeviceStatus(str, Enum):
    CONNECTED = "connected"
    PAIRED = "paired"


# ----------------------------------------------------------------- model ----

class _M(BaseModel):
    """Base model: strict, immutable, JSON-friendly."""
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        use_enum_values=True,
    )


class TvState(_M):
    """One TV's current display state."""
    power: bool
    station: StationKey
    """Which receiver's content is showing."""
    source: str
    """The input label on that receiver (e.g. 'PS5')."""
    model: str
    """Hardware model for display. Read-only."""


class BtDevice(_M):
    """A paired Bluetooth peer on a receiver."""
    id: str
    name: str
    battery: Optional[int] = None
    status: BtDeviceStatus = BtDeviceStatus.PAIRED


class AudioState(_M):
    """One receiver's audio state. Center has extra fields."""
    out: AudioOut
    active: Optional[str] = None
    """ID of the active Bluetooth device. None if no device active."""
    volume: int = Field(ge=0, le=100, default=50)
    muted: bool = False
    paired_devices: tuple[BtDevice, ...] = ()

    # Center-only — null on left/right.
    sound_field: Optional[SoundField] = None


class LightsState(_M):
    """Lutron room state."""
    scene: LightScene = LightScene.INTERMISSION
    level: int = Field(ge=0, le=100, default=40)


class ConnectionState(_M):
    """Per-device connectivity snapshot, refreshed on a 15s probe."""
    status: ConnectionStatus
    latency_ms: Optional[int] = None
    last_ok_s: Optional[int] = None
    """Seconds since last successful contact."""


class TheaterState(_M):
    """The full room state. Bumps `version` on every change."""
    version: int = 0
    tvs: dict[TvKey, TvState]
    audio: dict[StationKey, AudioState]
    lights: LightsState
    connections: dict[str, ConnectionState]


# ---------------------------------------------------- command request bodies

class TvPowerCmd(_M):
    power: bool


class TvSourceCmd(_M):
    station: StationKey
    source: str


class VolumeCmd(_M):
    volume: int = Field(ge=0, le=100)


class MuteCmd(_M):
    muted: bool


class AudioOutCmd(_M):
    out: AudioOut


class SoundFieldCmd(_M):
    sound_field: SoundField


class BtDeviceCmd(_M):
    device_id: str


class LightSceneCmd(_M):
    scene: LightScene


class LightLevelCmd(_M):
    level: int = Field(ge=0, le=100)


# ------------------------------------------------------ WebSocket envelopes -

WsType = Literal["state", "patch", "command", "ack", "ping", "pong"]


class WsState(_M):
    type: Literal["state"] = "state"
    api_version: int = API_VERSION
    state: TheaterState


class WsPatchOp(_M):
    op: Literal["replace", "add", "remove"]
    path: str
    value: object | None = None


class WsPatch(_M):
    type: Literal["patch"] = "patch"
    version: int
    ops: tuple[WsPatchOp, ...]


class WsCommand(_M):
    type: Literal["command"] = "command"
    id: str
    endpoint: str
    body: dict


class WsAck(_M):
    type: Literal["ack"] = "ack"
    id: str
    ok: bool
    error: Optional[str] = None


# --------------------------------------------------------------- error model

class ErrorResponse(_M):
    error: Literal[
        "not_found",
        "conflict",
        "device_offline",
        "device_error",
        "validation_error",
    ]
    message: str
    device: Optional[str] = None
