# //apps/theater — Handoff Package

This folder is a self-contained handoff from the design project to Claude Code.
Land it under `//apps/theater/` in the `unnatural_designs` monorepo and iterate
from there.

The design source-of-truth (the React prototype) lives one level up in this
project as `Theater Controls.html`. Visual changes happen there first; this
folder owns the runtime.

## What you have

| File                        | Purpose                                                      |
| --------------------------- | ------------------------------------------------------------ |
| `README.md`                 | This file. Start here.                                       |
| `RUNBOOK.md`                | Device pairing + deployment. Read once, refer often.         |
| `api-contract.md`           | REST + WebSocket protocol between frontend and service.      |
| `frontend-bridge.md`        | How to swap the prototype's `useTheater` mock for live data. |
| `service/`                  | Python service skeleton — FastAPI + per-device async clients.|
| `frontend/`                 | (Empty placeholder) — copy `variant-tiles.jsx` + `shared.jsx` here. |
| `deploy/Dockerfile`         | Single-image build (service + frontend bundle).              |
| `BUILD.bazel`               | Bazel target stubs.                                          |

## Architecture in one paragraph

A single FastAPI service runs on the always-on host in the theater. It owns
async clients for the 7 monitored devices (3 TVs, 3 receivers, 1 Lutron hub),
maintains the canonical room state in memory, and exposes two surfaces: a
REST API for one-shot reads and writes, and a WebSocket that streams the full
state on connect plus incremental patches on change. The React frontend (lifted
from the design project) is served from the same FastAPI app as static files
under `/`. There is no database — devices are the source of truth; the service
keeps a recent-good cache and a small SQLite file for pairing credentials and
device-to-name mappings.

## Why these choices

- **FastAPI**: the schema model from the design (Pydantic-shaped) ports cleanly,
  WebSocket support is first-class, and the type system gives us a free OpenAPI
  doc for free for the eventual integration tests.
- **`pylutron-caseta`**: speaks LEAP, supports RA3 processors, handles the
  pairing dance. The maintained client.
- **`aiowebostv`**: handles WS framing, pairing prompt, and the wake-on-LAN
  bridge for power-on. Saves us from re-implementing webOS's quirks.
- **Hand-rolled Sony clients**: Bravia IP Control and ESCAPI are both simple
  HTTP + PSK. The available libs are unmaintained or sync-only. ~200 LOC each.
- **In-memory state with WS fan-out**: classic single-room control surface
  pattern. No coordination cost, no consistency story to design.
- **Single container**: one process, one port, one thing to restart.

## Suggested iteration loop in Claude Code

```bash
cd ~/Work/UnnaturalDesigns/apps/theater
# 1. Pair devices once
bazel run //apps/theater/cmd/pair_devices -- --device lutron
bazel run //apps/theater/cmd/pair_devices -- --device tv_left
# (etc — see RUNBOOK.md)

# 2. Dev loop with hot reload
bazel run //apps/theater/service:dev   # uvicorn --reload, mock devices

# 3. Real-device dev (against the actual hardware)
bazel run //apps/theater/service:dev -- --real-devices

# 4. Tests
bazel test //apps/theater/...

# 5. Build the deployable image
bazel run //apps/theater/deploy:image_push
```

## Status at handoff

Every device module exports the right interface but raises `NotImplementedError`
inside. Wire one device at a time. The mock device implementations (`devices/_mock.py`)
let you develop the frontend bridge before any real hardware responds.

The prototype's data model is preserved exactly. State shape, station/zone
mapping, sound fields, lighting scenes — all faithful. If you change the schema
in `service/api/schema.py`, regenerate the TS types via the script in
`frontend-bridge.md`.

## Open decisions worth revisiting after one week of use

- **Authentication**. The service currently trusts the LAN. If guests join the
  Wi-Fi often, add a shared-secret header or session-cookie gate.
- **Bluetooth pairing UX**. New-device pairing on the Sony receivers requires
  the receiver's on-screen menu — there's no API path. The frontend's "Pair a
  new device" button currently writes a TODO log line; consider whether to keep
  it as a stub, hide it, or replace it with a "press receiver Bluetooth button
  for 5s" coach mark.
- **Persistence**. Volume/source state survives a service restart only if you
  poll devices on startup. Decide if you want to mirror device-reported state
  or push last-known state back to devices on reconnect.
