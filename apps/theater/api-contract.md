# API Contract

Single source of truth for the wire format between the frontend and the
service. The Pydantic models in `service/api/schema.py` derive this — when
the schema changes, regenerate the TS types via the script in
`frontend-bridge.md` and update this doc.

---

## Transport

**Base URL**: `http://<host>:8000`

Two surfaces, both required:

| Surface       | Use                                                                                     |
| ------------- | --------------------------------------------------------------------------------------- |
| `GET /api/state`      | Full snapshot. Used on startup or to recover from WS disconnect.                |
| `POST /api/commands/*`| One-shot writes. Idempotent. Returns the resulting state slice.                  |
| `WS /api/ws`          | Bidirectional. Server pushes `state` (full) on connect and `patch` (RFC 6902) on every change. Client may also send `command` envelopes. |

The frontend uses `WS` exclusively after the initial load. REST exists for
debugging (`curl`-able), for the eventual `theaterctl` CLI, and for the
connectivity probe.

## State envelope

The full state document is the same shape as the prototype's `useTheater()`
state, plus a `version` integer that bumps on every change.

```json
{
  "version": 142,
  "tvs": {
    "left":   { "power": true,  "station": "left",   "source": "PS5",         "model": "LG OLED55G4" },
    "center": { "power": true,  "station": "center", "source": "Handheld PC", "model": "Sony XBR-65A1E" },
    "right":  { "power": false, "station": "right",  "source": "ViewPort",    "model": "LG OLED55G4" }
  },
  "audio": {
    "left": {
      "out": "headphones",
      "active": "sony-xm5",
      "volume": 60,
      "muted": false,
      "paired_devices": [
        { "id": "sony-xm5",  "name": "Sony WH-1000XM5", "battery": 82, "status": "connected" },
        { "id": "visitor-1", "name": "Visitor (last)",   "battery": null, "status": "paired" }
      ]
    },
    "center": {
      "out": "speakers",
      "active": "airpods-max",
      "volume": 52,
      "muted": false,
      "sound_field": "atmos",
      "paired_devices": [ ... ]
    },
    "right": { "out": "headphones", ... }
  },
  "lights": { "scene": "intermission", "level": 40 },
  "connections": {
    "tv_left":     { "status": "connected", "latency_ms": 38, "last_ok_s": 2 },
    "tv_center":   { "status": "connected", "latency_ms": 21, "last_ok_s": 1 },
    "tv_right":    { "status": "connected", "latency_ms": 42, "last_ok_s": 4 },
    "rcvr_left":   { "status": "connected", "latency_ms": 56, "last_ok_s": 3 },
    "rcvr_center": { "status": "connected", "latency_ms": 18, "last_ok_s": 1 },
    "rcvr_right":  { "status": "connected", "latency_ms": 61, "last_ok_s": 5 },
    "lutron":      { "status": "connected", "latency_ms": 12, "last_ok_s": 2 }
  }
}
```

## Commands

Every command returns the resulting state slice for the affected scope.
Commands are also broadcast as WS `patch` messages to every other client.

### Displays

| Command                                    | Body                                              | Side effect                                |
| ------------------------------------------ | ------------------------------------------------- | ------------------------------------------ |
| `POST /api/commands/tv/{key}/power`        | `{ "power": bool }`                               | WoL packet + webOS/Bravia request          |
| `POST /api/commands/tv/{key}/source`       | `{ "station": "left|center|right", "source": str }` | Receiver input select on `station`, then TV input select on `key` if the TV is wired to a different receiver zone than its current source. |
| `POST /api/commands/all_off`               | (empty)                                           | Turns off all 3 TVs. **Does not touch lighting.** Receivers stay on (network-standby; cheap and lets next session be fast). |

`{key}` ∈ `left | center | right`. The combined source command is intentional:
the UI never makes the user think about "is this a receiver input or a TV
input." The service figures out which HDMI to switch the TV to based on the
station-to-zone mapping in `service/config.toml`.

### Audio

| Command                                          | Body                                                  |
| ------------------------------------------------ | ----------------------------------------------------- |
| `POST /api/commands/audio/{station}/volume`      | `{ "volume": int 0..100 }`                            |
| `POST /api/commands/audio/{station}/mute`        | `{ "muted": bool }`                                   |
| `POST /api/commands/audio/{station}/output`      | `{ "out": "speakers|headphones" }`                    |
| `POST /api/commands/audio/center/sound_field`    | `{ "sound_field": "atmos|dtsx|afd|multi|stereo|direct" }` |
| `POST /api/commands/audio/{station}/bt/activate` | `{ "device_id": str }`                                |
| `POST /api/commands/audio/{station}/bt/forget`   | `{ "device_id": str }`                                |

`{station}` ∈ `left | center | right`. `volume`, `sound_field` only valid on
`center` (others are headphones-only — request returns `409 Conflict`).

### Lighting

| Command                              | Body                                                |
| ------------------------------------ | --------------------------------------------------- |
| `POST /api/commands/lights/scene`    | `{ "scene": "allon|intermission|movie|off" }`       |
| `POST /api/commands/lights/level`    | `{ "level": int 0..100 }`                           |

Setting `level` implicitly clears the scene to `custom`.

### Connectivity

| Endpoint                          | Returns                                       |
| --------------------------------- | --------------------------------------------- |
| `GET /api/connectivity`           | The `connections` slice.                      |
| `POST /api/connectivity/recheck`  | Triggers a fresh probe of all devices.        |

The service also probes every 15s in the background; the indicator updates
without the user opening the panel.

## WebSocket protocol

### Server → client

On connect:
```json
{ "type": "state", "state": { ...full state... } }
```

On every state change:
```json
{
  "type": "patch",
  "version": 143,
  "ops": [
    { "op": "replace", "path": "/audio/center/volume", "value": 55 }
  ]
}
```

Patches use RFC 6902. The frontend MUST validate that `patch.version === local.version + 1`
or close and reconnect to resync.

Pings every 30s:
```json
{ "type": "ping", "ts": 1715817600 }
```

### Client → server

Any of the REST commands can also be sent over WS:
```json
{
  "type": "command",
  "id": "client-generated-uuid",
  "endpoint": "audio/center/volume",
  "body": { "volume": 55 }
}
```

Server acks:
```json
{ "type": "ack", "id": "client-generated-uuid", "ok": true }
```

Or:
```json
{ "type": "ack", "id": "client-generated-uuid", "ok": false, "error": "device offline" }
```

The frontend prefers WS commands because they're paired with the resulting
patch on the same socket — no race between the REST 200 and the WS broadcast.

## Errors

All REST errors are JSON:
```json
{ "error": "string code", "message": "human-readable", "device": "tv_left" }
```

| Code           | When                                                            | UI behaviour                          |
| -------------- | --------------------------------------------------------------- | ------------------------------------- |
| `not_found`    | TV/station key doesn't exist.                                   | Shouldn't happen; log + ignore.       |
| `conflict`     | E.g. `volume` on a headphones-only station.                     | UI should disable the control.        |
| `device_offline` | The device isn't responding right now.                        | Show a transient toast; mark in conn. |
| `device_error` | The device returned a protocol error.                           | Same as above + log raw response.     |

## Versioning

The schema lives in `service/api/schema.py`. Bump `API_VERSION` in that file
on any breaking change. The WS server includes it in the `state` envelope
under `_api_version`; the frontend refuses to connect on mismatch and shows
a "reload" prompt.
