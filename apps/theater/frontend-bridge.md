# Frontend Bridge — Swapping the Mock for the Live Service

This document is the bridge between the design prototype in this project and
the runtime service shipped in `//apps/theater/service`. Copy
`Theater Controls.html`, `shared.jsx`, `variant-tiles.jsx`, and the
`design-system/` folder from the design project into
`//apps/theater/frontend/` and apply the changes below.

## What changes

The only file that changes meaningfully is `shared.jsx`. The `useTheater`
hook currently holds local React state and mutates it; we swap that for a
WebSocket-backed store.

Component files (`variant-tiles.jsx`) don't change. The hook contract is
preserved — same `state`, same `setTvPower`, `setTvSource`, etc.

## The hook swap

Replace the body of `useTheater` with this:

```jsx
function useTheater() {
  const [state, setState] = useState(initialEmptyState);
  const [connected, setConnected] = useState(false);
  const ws = useRef(null);
  const pending = useRef(new Map());  // id -> resolve

  // Connect on mount; reconnect with exponential backoff.
  useEffect(() => {
    let backoff = 250;
    let alive = true;
    let socket;

    const open = () => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${proto}//${location.host}/api/ws`);
      ws.current = socket;

      socket.onopen = () => { backoff = 250; setConnected(true); };

      socket.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "state") {
          setState(msg.state);
        } else if (msg.type === "patch") {
          setState((s) => applyPatch(s, msg.ops, msg.version));
        } else if (msg.type === "ack") {
          const r = pending.current.get(msg.id);
          if (r) { pending.current.delete(msg.id); r(msg); }
        } else if (msg.type === "ping") {
          socket.send(JSON.stringify({ type: "pong" }));
        }
      };

      socket.onclose = () => {
        setConnected(false);
        if (!alive) return;
        setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, 8000);
      };
    };

    open();
    return () => { alive = false; socket?.close(); };
  }, []);

  // Send a command; resolve when the server acks.
  const cmd = useCallback((endpoint, body) => {
    return new Promise((resolve, reject) => {
      const id = crypto.randomUUID();
      pending.current.set(id, (ack) => ack.ok ? resolve() : reject(new Error(ack.error)));
      ws.current?.send(JSON.stringify({ type: "command", id, endpoint, body }));
      setTimeout(() => {
        if (pending.current.delete(id)) reject(new Error("timeout"));
      }, 5000);
    });
  }, []);

  // Preserve the previous API surface so variant-tiles.jsx doesn't change.
  return {
    state,
    connected,
    setTvPower:    (tv, power)        => cmd(`tv/${tv}/power`,  { power }),
    setTvSource:   (tv, station, source) => cmd(`tv/${tv}/source`, { station, source }),
    allOff:        ()                 => cmd("all_off", {}),
    setVolume:     (station, volume)  => cmd(`audio/${station}/volume`, { volume }),
    toggleMute:    (station)          => cmd(`audio/${station}/mute`,   { muted: !state.audio[station].muted }),
    setAudioOut:   (station, out)     => cmd(`audio/${station}/output`, { out }),
    setSoundField: (sf)               => cmd("audio/center/sound_field", { sound_field: sf }),
    setActiveDevice: (station, id)    => cmd(`audio/${station}/bt/activate`, { device_id: id }),
    forgetDevice:  (station, id)      => cmd(`audio/${station}/bt/forget`,   { device_id: id }),
    setLights:     (scene)            => cmd("lights/scene", { scene }),
    setLightsLevel:(level)            => cmd("lights/level", { level }),
  };
}
```

Helper `applyPatch` is roughly 30 LOC of standard RFC 6902:

```js
function applyPatch(state, ops, expectedVersion) {
  if (expectedVersion !== state.version + 1) {
    // The server skipped a version (we dropped a frame, or someone reconnected
    // mid-mutation). Best to just close and resync.
    console.warn("version skew; requesting full state");
    fetch("/api/state").then(r => r.json()).then(setState);
    return state;
  }
  const next = structuredClone(state);
  next.version = expectedVersion;
  for (const op of ops) {
    const parts = op.path.split("/").filter(Boolean);
    let cur = next;
    for (let i = 0; i < parts.length - 1; i++) cur = cur[parts[i]];
    const last = parts[parts.length - 1];
    if (op.op === "remove") delete cur[last];
    else cur[last] = op.value;
  }
  return next;
}
```

## Pessimistic vs optimistic updates

The hook above is **pessimistic** — the UI doesn't move until the server
broadcasts a patch. This is correct for routing changes (where the result
depends on the receiver actually completing the input switch), but feels
laggy for the volume slider.

Add optimistic local state for the volume control specifically:

```jsx
function VolumeSlider({ value, onChange }) {
  const [localValue, setLocalValue] = useState(value);
  const [dragging, setDragging] = useState(false);
  // While dragging, show local; otherwise show server-truth.
  const displayed = dragging ? localValue : value;
  // ...
}
```

This is the only spot it matters. Everything else should stay pessimistic so
the UI never lies about what the room is doing.

## Connection state UI

The hook exposes `connected` — wire it into the header's status pill. When
the WS is down, show "offline" regardless of what `state.connections` says
(stale).

## Regenerating TypeScript types from the Python schema

The frontend can stay in plain JSX, but if we want types:

```bash
# In //apps/theater/:
bazel run //apps/theater/scripts:gen_ts_types > frontend/types/state.ts
```

The script uses Pydantic's `model_json_schema()` + `quicktype` to emit
`state.ts`. Re-run after any change to `service/api/schema.py`.

## Things the bridge does NOT need to do

- **Routing logic.** The frontend sends `{station, source}`; the service
  decides which HDMI port to set on which device. The frontend has no
  knowledge of HDMI port numbers.
- **State persistence.** The service is source of truth.
- **Authentication.** Currently LAN-trusted (see README "Open decisions").
- **Polling.** The WS push is exhaustive — no `setInterval` over `/state`.

## Smoke test the bridge

With the service running against mock devices (`bazel run //apps/theater/service:dev`),
open `http://localhost:8000/`. The UI should connect, populate, and respond
to interactions. The mocks record every call; check `service/devices/_mock.py`
docstrings for how to inspect the call log in a test.
