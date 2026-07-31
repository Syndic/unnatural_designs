# Theater Controls — Device Pairing & Deployment Runbook

Read this once front to back. Then refer to the section you need.

---

## 0. Prerequisites

- Linux/macOS host on the theater's LAN. Static IP recommended (DHCP reservation
  works fine).
- Network reachability: the host must be able to open outbound TCP to every
  device, and Wake-on-LAN UDP broadcast must be permitted on the segment for
  TV power-on to work.
- Docker (for production) or Python 3.13+ (for dev).
- One device-by-device pairing pass. Plan for ~30 minutes the first time.

## 1. Network discovery

Before pairing, confirm every device's IP. Either reserve them in your router or
run:

```bash
bazel run //apps/theater/cmd/theaterctl -- discover
```

Expected output: 7 devices found (3 LG/Sony TVs, 3 Sony receivers, 1 Lutron
processor), each with model + MAC + IP. Write them into `service/config.toml`:

```toml
[devices.tv_left]
host = "10.0.10.21"
mac  = "ac:f1:08:xx:xx:xx"   # for Wake-on-LAN
model = "LG OLED55G4"

[devices.tv_center]
host = "10.0.10.22"
model = "Sony XBR-65A1E"

# ... etc
```

## 2. Per-device pairing

### 2a. LG G4 TVs (Left, Right) — webOS

LG TVs require a one-time prompt-accept on the TV itself.

1. On the TV: `Settings → General → External Devices → Mobile TV On` → ON.
   This is what makes Wake-on-LAN work.
2. Run pairing:
   ```bash
   bazel run //apps/theater/cmd/pair_devices -- --device tv_left
   ```
3. The TV will pop a "do you want to allow this device to control your TV"
   prompt. Accept with the remote. The CLI stores the issued client key under
   `~/.config/theater/credentials.json`.
4. Repeat for `tv_right`.

If the pairing dialog never appears, the TV's "Mobile TV On" toggle is off, or
the TV's IP changed. Re-run discovery.

### 2b. Sony A1E TV (Center) — Bravia IP Control

Sony's BRAVIA TVs use a pre-shared key.

1. On the TV: `Settings → Network → Home network → IP control`.
2. Set "Authentication" to `Normal and Pre-Shared Key`.
3. Choose a PSK (suggest a 16-char hex string from your password manager).
4. `Settings → Network → Home network → Remote start` → ON (enables WoL).
5. Run pairing:
   ```bash
   bazel run //apps/theater/cmd/pair_devices -- --device tv_center
   ```
   Paste the PSK when prompted. The CLI confirms by issuing a `system.getInterfaceInformation`
   request and stores the PSK in `credentials.json`.

### 2c. Sony STR-AZ receivers (Left, Center, Right) — ESCAPI

Each receiver uses ESCAPI's PSK auth, same flow as Bravia.

1. On the receiver's web UI (`http://<receiver-ip>/`): `Network Settings →
   IP Control`.
2. Enable IP Control. Set a pre-shared key.
3. While you're in the web UI, confirm `Network Standby` is ON (lets the receiver
   accept commands while off).
4. Run pairing:
   ```bash
   bazel run //apps/theater/cmd/pair_devices -- --device rcvr_left
   ```
   Paste the PSK. The CLI confirms by querying the receiver's current input and
   sound field.
5. Repeat for `rcvr_center` and `rcvr_right`.

### 2d. Lutron RA3 — LEAP

Lutron's pairing is cert-based and the most involved.

1. The Lutron processor must be online and the system commissioned via the
   Lutron Designer app. Confirm via the processor's web UI.
2. Run pairing:
   ```bash
   bazel run //apps/theater/cmd/pair_devices -- --device lutron
   ```
3. When prompted, **press the physical button on the Lutron processor** within
   30 seconds. The pairing tool generates a CSR, presents it, gets a signed
   cert back, and stores the keypair under `~/.config/theater/lutron/`.
4. The pairing tool then lists every dimmer/keypad/scene it can see. Copy the
   scene IDs you care about (`All On`, `Intermission`, `Movie`, `Room Off`)
   into `service/config.toml`:
   ```toml
   [lutron.scenes]
   allon        = "scene/12"   # IDs from the pairing output
   intermission = "scene/13"
   movie        = "scene/14"
   off          = "scene/15"

   [lutron.dimmer]
   id = "device/27"            # The "room dimmer" load
   ```

## 3. Smoke test

```bash
bazel run //apps/theater/cmd/theaterctl -- status
```

Expected: all 7 devices report `connected` with reasonable latency. If any
device shows `offline`, the connectivity panel in the UI will too — that's the
green-square-of-truth.

```bash
bazel run //apps/theater/cmd/theaterctl -- tv center on
bazel run //apps/theater/cmd/theaterctl -- tv center source 'Handheld PC'
bazel run //apps/theater/cmd/theaterctl -- audio center volume 40
bazel run //apps/theater/cmd/theaterctl -- lights movie
```

Each command should produce the visible result in the room. If anything is
silent (no error, no effect), check the device's web UI for the actual current
state — the service might be reporting cache-stale data.

## 4. Run the service in dev mode

```bash
bazel run //apps/theater/service:dev -- --real-devices
```

This starts `uvicorn` with `--reload` against the real devices. Visit
`http://localhost:8000/` for the UI. The service logs every device command
issued in mono.

## 5. Build and deploy

### 5a. Container

```bash
bazel run //apps/theater/deploy:image_push -- --tag latest
```

This produces an OCI image with:
- The FastAPI service.
- The compiled frontend bundle.
- `~/.config/theater/` baked-in as a volume mount point — credentials must be
  mounted at runtime, not built in.

### 5b. Docker run

```bash
docker run -d \
  --name theater \
  --restart unless-stopped \
  --network host \
  -v /var/lib/theater:/root/.config/theater \
  -e THEATER_CONFIG=/root/.config/theater/config.toml \
  ghcr.io/syndic/theater:latest
```

`--network host` is required: Wake-on-LAN needs raw broadcast access, and mDNS
discovery for Lutron likewise. Without it, only manual-IP devices work.

### 5c. Kubernetes (when ready)

Manifests under `deploy/k8s/` use a `hostNetwork: true` Deployment. Same WoL
caveat. The PVC for `/root/.config/theater` should be backed by a Local PV
since the credentials are host-specific.

## 6. Troubleshooting

| Symptom                                          | Likely cause                                  | Fix                                                                     |
| ------------------------------------------------ | --------------------------------------------- | ----------------------------------------------------------------------- |
| TV power-on fails, power-off works               | Wake-on-LAN broken                            | Confirm host has L2 access to TV segment; container needs host network. |
| Receiver shows wrong source for 2-3 seconds      | Receiver reporting cached input               | Expected. UI debounces.                                                 |
| Lutron `connected` but scene buttons do nothing  | Scene IDs in config don't match the processor | Re-run `pair_devices --device lutron --list`, copy fresh IDs.           |
| New BT device won't pair via the app             | Sony API limitation                           | Pair from the receiver's front panel; it then appears in the app's list.|
| UI loads but `/api/state` returns 503            | One device fully offline; agg = `offline`     | Click the status pill — the device list shows which.                    |
| Volume slider jumps after release                | Slow receiver ACK race                        | Bump `RECEIVER_DEBOUNCE_MS` in `config.toml`.                           |

## 7. Rollback

The container is stateless. Roll back by retagging:

```bash
docker stop theater && docker rm theater
docker run ... ghcr.io/syndic/theater:<previous-sha>
```

Credentials persist in the mounted volume. Re-pair is only needed if a device
itself was factory-reset.
