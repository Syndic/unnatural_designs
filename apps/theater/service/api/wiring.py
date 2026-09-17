"""Static wiring of the theater — the part of the world that isn't state.

These mirror the constants in the frontend prototype's shared.jsx. If a TV
or receiver moves or the HDMI matrix changes, this is the only file to edit.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import StationKey, TvKey


@dataclass(frozen=True)
class StationDef:
    name: str
    receiver_model: str
    sources: tuple[str, ...]
    """Source labels on this receiver, in display order."""


@dataclass(frozen=True)
class TvInput:
    label: str
    """HDMI port label (e.g. 'HDMI 2')."""
    station: StationKey
    """Which receiver this HDMI port is wired to."""
    zone: int
    """Which output zone on that receiver (1 = main, 2 = zone 2)."""


@dataclass(frozen=True)
class TvDef:
    name: str
    model: str
    inputs: tuple[TvInput, ...]


STATIONS: dict[StationKey, StationDef] = {
    StationKey.LEFT: StationDef(
        name="Left",
        receiver_model="STR-AZ1000ES",
        sources=("Switch", "PS5", "Xbox", "PC", "Visitor"),
    ),
    StationKey.CENTER: StationDef(
        name="Center",
        receiver_model="STR-AZ5000ES",
        sources=("Switch", "PS5", "Xbox", "PC", "Handheld PC", "Visitor"),
    ),
    StationKey.RIGHT: StationDef(
        name="Right",
        receiver_model="STR-AZ1000ES",
        sources=("Switch", "PS5", "Xbox", "PC", "Visitor", "ViewPort"),
    ),
}


TVS: dict[TvKey, TvDef] = {
    TvKey.LEFT: TvDef(
        name="Left",
        model="LG G4 55\u2033",
        inputs=(
            TvInput(label="HDMI 1", station=StationKey.LEFT,   zone=1),
            TvInput(label="HDMI 2", station=StationKey.CENTER, zone=2),
        ),
    ),
    TvKey.CENTER: TvDef(
        name="Center",
        model="Sony A1E 65\u2033",
        inputs=(
            TvInput(label="HDMI 1", station=StationKey.CENTER, zone=1),
            TvInput(label="HDMI 2", station=StationKey.LEFT,   zone=2),
            TvInput(label="HDMI 3", station=StationKey.RIGHT,  zone=2),
        ),
    ),
    TvKey.RIGHT: TvDef(
        name="Right",
        model="LG G4 55\u2033",
        inputs=(
            TvInput(label="HDMI 1", station=StationKey.RIGHT, zone=1),
        ),
    ),
}


def resolve_tv_hdmi(tv: TvKey, station: StationKey) -> TvInput:
    """Find which HDMI port on `tv` carries the named `station`'s content.

    Raises KeyError if the TV isn't wired to that station — the UI should
    never let the user pick an impossible combination, but the service
    enforces it as a 409.
    """
    for inp in TVS[tv].inputs:
        if inp.station == station:
            return inp
    raise KeyError(f"{tv.value} TV has no input from {station.value}")
