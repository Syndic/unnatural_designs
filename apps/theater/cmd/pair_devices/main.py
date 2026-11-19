"""One-time pairing CLI.

  bazel run //apps/theater/cmd/pair_devices -- --device lutron
  bazel run //apps/theater/cmd/pair_devices -- --device tv_left

Writes credentials to ~/.config/theater/credentials.json (and Lutron certs
under ~/.config/theater/lutron/). The service reads these at startup.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

CRED_DIR = Path(os.environ.get("THEATER_CONFIG", Path.home() / ".config" / "theater"))
CRED_FILE = CRED_DIR / "credentials.json"


def _load_creds() -> dict:
    if CRED_FILE.exists():
        return json.loads(CRED_FILE.read_text())
    return {}


def _save_creds(creds: dict) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(json.dumps(creds, indent=2))
    CRED_FILE.chmod(0o600)
    print(f"wrote credentials to {CRED_FILE}")


# ----- per-device pairing -----------------------------------------------------

async def pair_lg(host: str) -> dict:
    """Connect to a webOS TV, trigger the on-TV prompt, store the client key."""
    print(f"Connecting to LG TV at {host}.")
    print("Watch the TV — accept the pairing prompt with the remote.")
    # TODO(claude-code): use aiowebostv:
    #   client = WebOsClient(host)
    #   await client.connect()         # blocks until user accepts
    #   key = client.client_key
    #   await client.disconnect()
    raise NotImplementedError


async def pair_bravia(host: str) -> dict:
    """Sony Bravia uses a PSK set in the TV menu. Verify it works."""
    psk = getpass.getpass("Bravia PSK (from TV's IP Control menu): ")
    # TODO(claude-code): POST to /sony/system getInterfaceInformation with the
    # PSK; raise on auth failure.
    return {"psk": psk}


async def pair_escapi(host: str) -> dict:
    """Sony receivers — same pattern as Bravia."""
    psk = getpass.getpass("Receiver PSK (from web UI IP Control): ")
    # TODO(claude-code): verify by querying current input on zone 1.
    return {"psk": psk}


async def pair_lutron(host: str) -> dict:
    """Run the LEAP pairing dance: generate CSR, ask the processor to sign it,
    save the resulting cert + key under ~/.config/theater/lutron/."""
    print(f"Pairing with Lutron at {host}.")
    print("Press the button on the Lutron processor within 30 seconds...")
    # TODO(claude-code):
    #   from pylutron_caseta.pairing import async_pair
    #   data = await async_pair(host)
    #   write data["ca"], data["cert"], data["key"] to CRED_DIR/lutron/
    cert_dir = CRED_DIR / "lutron"
    cert_dir.mkdir(parents=True, exist_ok=True)
    print("Pairing successful — now listing devices/scenes...")
    # TODO(claude-code): connect via Smartbridge, list scenes + dimmers,
    # print their IDs so the user can paste them into config.toml.
    return {"cert_dir": str(cert_dir)}


HANDLERS = {
    "tv_left":     pair_lg,
    "tv_right":    pair_lg,
    "tv_center":   pair_bravia,
    "rcvr_left":   pair_escapi,
    "rcvr_center": pair_escapi,
    "rcvr_right":  pair_escapi,
    "lutron":      pair_lutron,
}


def main() -> int:
    p = argparse.ArgumentParser(description="Theater Controls device pairing")
    p.add_argument("--device", required=True, choices=sorted(HANDLERS),
                   help="Which device to pair.")
    p.add_argument("--host", help="Override host IP from config.toml.")
    args = p.parse_args()

    # TODO(claude-code): if --host not given, load config.toml and look up.
    if not args.host:
        print("--host is currently required (config.toml loader pending)",
              file=sys.stderr)
        return 2

    creds = _load_creds()
    result = asyncio.run(HANDLERS[args.device](args.host))
    creds[args.device] = {"host": args.host, **result}
    _save_creds(creds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
