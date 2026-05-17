"""Integration tests: drive the service against the mock devices and assert
the right room state results.

Run:
  bazel test //apps/theater:integration_test
"""

from __future__ import annotations

import asyncio

import pytest

from .api.schema import AudioOut, LightScene, SoundField, StationKey, TvKey
from .devices._mock import make_mock_devices
from .state import Room, initial_state


@pytest.fixture
async def room():
    devices = make_mock_devices()
    r = Room(devices, initial_state())
    await r.start()
    yield r
    await r.stop()


@pytest.mark.asyncio
async def test_tv_power_toggle(room):
    await room.tv_power(TvKey.LEFT, True)
    assert room.state.tvs[TvKey.LEFT].power is True
    await room.tv_power(TvKey.LEFT, False)
    assert room.state.tvs[TvKey.LEFT].power is False


@pytest.mark.asyncio
async def test_tv_source_sets_receiver_then_tv(room):
    # Routing center TV to look at the LEFT station's content via Zone 2.
    await room.tv_source(TvKey.CENTER, StationKey.LEFT, "PS5")

    # Receiver got told to put PS5 on zone 2.
    left_rcvr = room._devices["rcvr_left"]
    assert ("set_input", {"zone": 2, "source": "PS5"}) in left_rcvr.log.calls

    # TV got told to switch to HDMI 2 (where the LEFT zone 2 is wired).
    center_tv = room._devices["tv_center"]
    assert ("select_input", {"hdmi_label": "HDMI 2"}) in center_tv.log.calls

    # State reflects.
    assert room.state.tvs[TvKey.CENTER].station == StationKey.LEFT.value
    assert room.state.tvs[TvKey.CENTER].source == "PS5"


@pytest.mark.asyncio
async def test_all_off_does_not_touch_lights(room):
    await room.set_scene(LightScene.MOVIE)
    scene_before = room.state.lights.scene
    await room.all_off()
    assert all(not tv.power for tv in room.state.tvs.values())
    assert room.state.lights.scene == scene_before


@pytest.mark.asyncio
async def test_volume_only_on_center(room):
    await room.set_volume(StationKey.CENTER, 33)
    assert room.state.audio[StationKey.CENTER.value].volume == 33

    with pytest.raises(ValueError):
        await room.set_volume(StationKey.LEFT, 33)


@pytest.mark.asyncio
async def test_sound_field_only_on_center(room):
    await room.set_sound_field(SoundField.DTSX)
    assert room.state.audio[StationKey.CENTER.value].sound_field == SoundField.DTSX.value


@pytest.mark.asyncio
async def test_patch_broadcasts(room):
    received: list[tuple[int, list]] = []

    async def listener(version, ops):
        received.append((version, list(ops)))

    room.subscribe(listener)
    await room.tv_power(TvKey.LEFT, True)
    # Let the broadcast settle.
    await asyncio.sleep(0)
    assert any(o.path == "/tvs/left/power" for _, ops in received for o in ops)
