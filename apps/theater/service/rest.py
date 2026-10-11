"""REST routes. Thin facade over Room — every endpoint validates, calls
Room.<verb>, returns the resulting state slice.

The frontend prefers WebSocket commands; REST exists for theaterctl, curl
debugging, and the connectivity panel's manual recheck.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .api.schema import (
    AudioOutCmd, BtDeviceCmd, LightLevelCmd, LightSceneCmd, MuteCmd,
    SoundFieldCmd, StationKey, TheaterState, TvKey, TvPowerCmd, TvSourceCmd,
    VolumeCmd,
)
from .state import Room


def make_router(room_provider) -> APIRouter:
    """`room_provider`: callable that returns the current Room. Lets us
    inject a mock in tests."""
    r = APIRouter(prefix="/api")

    def room() -> Room:
        return room_provider()

    @r.get("/state", response_model=TheaterState)
    async def get_state():
        return room().state

    @r.get("/connectivity")
    async def get_connectivity():
        return room().state.connections

    @r.post("/connectivity/recheck")
    async def recheck():
        # TODO(claude-code): trigger a one-shot probe pass; for now no-op.
        return {"ok": True}

    # ----- TVs ----------------------------------------------------------

    @r.post("/commands/tv/{tv_key}/power")
    async def tv_power(tv_key: TvKey, body: TvPowerCmd):
        try:
            await room().tv_power(tv_key, body.power)
        except Exception as e:
            raise HTTPException(502, str(e))
        return {"ok": True}

    @r.post("/commands/tv/{tv_key}/source")
    async def tv_source(tv_key: TvKey, body: TvSourceCmd):
        try:
            await room().tv_source(tv_key, body.station, body.source)
        except KeyError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @r.post("/commands/all_off")
    async def all_off():
        await room().all_off()
        return {"ok": True}

    # ----- Audio --------------------------------------------------------

    @r.post("/commands/audio/{station}/volume")
    async def audio_volume(station: StationKey, body: VolumeCmd):
        try:
            await room().set_volume(station, body.volume)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @r.post("/commands/audio/{station}/mute")
    async def audio_mute(station: StationKey, body: MuteCmd):
        await room().set_mute(station, body.muted)
        return {"ok": True}

    @r.post("/commands/audio/{station}/output")
    async def audio_output(station: StationKey, body: AudioOutCmd):
        try:
            await room().set_audio_out(station, body.out)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @r.post("/commands/audio/center/sound_field")
    async def audio_sound_field(body: SoundFieldCmd):
        await room().set_sound_field(body.sound_field)
        return {"ok": True}

    @r.post("/commands/audio/{station}/bt/activate")
    async def bt_activate(station: StationKey, body: BtDeviceCmd):
        await room().activate_bt(station, body.device_id)
        return {"ok": True}

    @r.post("/commands/audio/{station}/bt/forget")
    async def bt_forget(station: StationKey, body: BtDeviceCmd):
        await room().forget_bt(station, body.device_id)
        return {"ok": True}

    # ----- Lights -------------------------------------------------------

    @r.post("/commands/lights/scene")
    async def lights_scene(body: LightSceneCmd):
        await room().set_scene(body.scene)
        return {"ok": True}

    @r.post("/commands/lights/level")
    async def lights_level(body: LightLevelCmd):
        await room().set_level(body.level)
        return {"ok": True}

    return r
