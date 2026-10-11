# //apps/theater/frontend

The compiled React app lives here. At handoff time this folder is **empty**
except for this README.

## How to populate it

From the design project, copy:

- `Theater Controls.html`            → `frontend/index.html`
- `shared.jsx`                       → `frontend/shared.jsx`
- `variant-tiles.jsx`                → `frontend/variant-tiles.jsx`
  (the other two variants — `variant-flow.jsx`, `variant-terminal.jsx` —
  are not needed at runtime; leave them in the design project as reference)
- `design-system/`                   → `frontend/design-system/`

Then apply the hook swap from `../handoff/frontend-bridge.md` to
`shared.jsx`.

## Why no build step

The prototype is already plain HTML + Babel-in-the-browser. For a
single-room control surface served on a LAN this is plenty fast — the entire
bundle is ~140KB before gzip and the user opens it once per session.

If page-load latency ever becomes a complaint, swap to Vite:

```bash
npm create vite@latest -- --template react
# move shared.jsx + variant-tiles.jsx + design-system into src/
npm run build   # outputs to dist/
```

…and point `service/main.py`'s `StaticFiles` mount at `dist/`. No source
changes needed — the components are framework-agnostic React.

## Why this isn't `//libs/frontend`

The frontend is genuinely app-specific (it references theater wiring). When
a second control-surface app arrives (likely a different room), factor the
shared bits into `//libs/frontend/controls/`.
