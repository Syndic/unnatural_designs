"""theaterctl — one-shot CLI for manual control and debugging.

  bazel run //apps/theater/cmd/theaterctl -- status
  bazel run //apps/theater/cmd/theaterctl -- tv center on
  bazel run //apps/theater/cmd/theaterctl -- tv center source 'Handheld PC'
  bazel run //apps/theater/cmd/theaterctl -- audio center volume 40
  bazel run //apps/theater/cmd/theaterctl -- lights movie

Talks to the service over REST at http://localhost:8000 (override with
THEATER_HOST). For pairing, see //apps/theater/cmd/pair_devices.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


HOST = os.environ.get("THEATER_HOST", "http://localhost:8000")


def _post(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{HOST}{path}",
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "{}")


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{HOST}{path}") as r:
        return json.loads(r.read())


# ----- commands -------------------------------------------------------------

def cmd_status(args) -> int:
    state = _get("/api/state")
    print(f"version: {state['version']}")
    print()
    print("TVs:")
    for k, tv in state["tvs"].items():
        print(f"  {k:7s}  power={tv['power']:5}  {tv['station']:6}  {tv['source']}")
    print()
    print("Audio:")
    for k, a in state["audio"].items():
        extra = ""
        if a.get("sound_field"): extra = f"  sf={a['sound_field']}"
        if k == "center": extra += f"  vol={a['volume']}"
        muted = " [muted]" if a["muted"] else ""
        print(f"  {k:7s}  out={a['out']:10}{extra}{muted}")
    print()
    print(f"Lights: {state['lights']['scene']}  @ {state['lights']['level']}%")
    print()
    print("Connectivity:")
    for k, c in state["connections"].items():
        lat = f"{c['latency_ms']}ms" if c.get("latency_ms") is not None else "--"
        print(f"  {k:13s} {c['status']:10}  {lat}")
    return 0


def cmd_tv(args) -> int:
    if args.action == "on":
        _post(f"/api/commands/tv/{args.tv}/power", {"power": True})
    elif args.action == "off":
        _post(f"/api/commands/tv/{args.tv}/power", {"power": False})
    elif args.action == "source":
        _post(f"/api/commands/tv/{args.tv}/source",
              {"station": args.station, "source": args.value})
    return 0


def cmd_audio(args) -> int:
    if args.action == "volume":
        _post(f"/api/commands/audio/{args.station}/volume", {"volume": int(args.value)})
    elif args.action == "mute":
        _post(f"/api/commands/audio/{args.station}/mute",   {"muted": args.value == "on"})
    elif args.action == "output":
        _post(f"/api/commands/audio/{args.station}/output", {"out": args.value})
    return 0


def cmd_lights(args) -> int:
    if args.scene:
        _post("/api/commands/lights/scene", {"scene": args.scene})
    if args.level is not None:
        _post("/api/commands/lights/level", {"level": args.level})
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="theaterctl")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Print room state.").set_defaults(fn=cmd_status)

    p_tv = sub.add_parser("tv")
    p_tv.add_argument("tv", choices=["left", "center", "right"])
    p_tv.add_argument("action", choices=["on", "off", "source"])
    p_tv.add_argument("station", nargs="?", help="For 'source': station to use.")
    p_tv.add_argument("value", nargs="?", help="For 'source': source label.")
    p_tv.set_defaults(fn=cmd_tv)

    p_au = sub.add_parser("audio")
    p_au.add_argument("station", choices=["left", "center", "right"])
    p_au.add_argument("action", choices=["volume", "mute", "output"])
    p_au.add_argument("value", help="Value for the action.")
    p_au.set_defaults(fn=cmd_audio)

    p_li = sub.add_parser("lights")
    p_li.add_argument("scene", nargs="?", choices=["allon", "intermission", "movie", "off"])
    p_li.add_argument("--level", type=int)
    p_li.set_defaults(fn=cmd_lights)

    args = p.parse_args()
    try:
        return args.fn(args)
    except urllib.error.URLError as e:
        print(f"could not reach service at {HOST}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
