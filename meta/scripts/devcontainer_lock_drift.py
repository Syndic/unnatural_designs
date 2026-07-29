#!/usr/bin/env python3
"""Classify `devcontainer outdated --output-format json` into actionable vs. informational drift.

`.devcontainer/devcontainer-lock.json` pins a resolved version + digest per feature. Nothing
updates it on its own: Renovate's `devcontainer` manager reads only the feature *references* in
`devcontainer.json` (floating major tags like `:2`), and no manager parses the lock. This script is
the drift detector for devcontainer-lock.yml, which runs it on a schedule and opens a PR.

The one decision that matters here is the split between two columns the CLI reports:

  - **`wanted`** is the newest version *within the major the reference pins*. `devcontainer
    upgrade` moves `current` to `wanted`, so `current != wanted` is drift this repo can act on,
    and it is the only thing that should open a PR.
  - **`latest`** may cross a major boundary (`common-utils:2` while `3.x` exists). No amount of
    `devcontainer upgrade` reaches it — the fix is bumping the reference in `devcontainer.json`,
    which is Renovate's job. Reporting it as actionable would produce a workflow that opens PRs
    it cannot fulfil, so it is surfaced as a note instead.

Pure functions carry the logic and the tests exercise them without the CLI or the Actions
environment — the same split as classify_changed_paths.py.

Usage:
  devcontainer outdated --workspace-folder . --output-format json | \\
    python3 meta/scripts/devcontainer_lock_drift.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# ── Pure functions (the part the tests exercise) ──────────────────────────────


def find_drift(features: dict[str, dict[str, str]]) -> list[tuple[str, str, str]]:
    """Return `(feature, current, wanted)` for each feature `devcontainer upgrade` would move.

    Features missing either field are skipped rather than guessed at: an entry the CLI could not
    resolve is not evidence of drift, and treating it as such would open an empty PR.
    """
    out = []
    for name, info in sorted(features.items()):
        current, wanted = info.get("current"), info.get("wanted")
        if current and wanted and current != wanted:
            out.append((name, current, wanted))
    return out


def find_major_available(features: dict[str, dict[str, str]]) -> list[tuple[str, str, str]]:
    """Return `(feature, wantedMajor, latestMajor)` where a newer major exists behind the pin.

    Not actionable by `devcontainer upgrade` — the reference in devcontainer.json has to move.
    """
    out = []
    for name, info in sorted(features.items()):
        wanted_major, latest_major = info.get("wantedMajor"), info.get("latestMajor")
        if wanted_major and latest_major and wanted_major != latest_major:
            out.append((name, wanted_major, latest_major))
    return out


def format_summary(
    drift: list[tuple[str, str, str]],
    major: list[tuple[str, str, str]],
) -> str:
    """Render the PR body: a table of what `upgrade` moves, plus any out-of-reach majors."""
    lines = [
        "`devcontainer upgrade` refreshed the feature lock to the newest versions available",
        "within each pinned major.",
        "",
        "| Feature | From | To |",
        "| --- | --- | --- |",
    ]
    lines += [f"| `{name}` | {current} | {wanted} |" for name, current, wanted in drift]
    if major:
        lines += [
            "",
            "A newer **major** also exists for the features below. `devcontainer upgrade` cannot",
            "reach it — that needs the reference in `devcontainer.json` to move, which is",
            "Renovate's `devcontainer` manager's job, not this workflow's.",
            "",
        ]
        lines += [f"- `{name}`: pinned at `{w}`, `{latest}` available" for name, w, latest in major]
    return "\n".join(lines)


def format_outputs(drift: list[tuple[str, str, str]], summary: str) -> str:
    """Render GitHub Actions step outputs; `summary` uses the heredoc form (it is multiline)."""
    return f"drift={'true' if drift else 'false'}\nsummary<<SUMMARY_EOF\n{summary}\nSUMMARY_EOF\n"


# ── I/O wrappers (thin shells around file I/O) ────────────────────────────────


def _emit_github_output(text: str) -> None:
    """Append step outputs to $GITHUB_OUTPUT; print to stderr if unset (local invocation)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"GITHUB_OUTPUT not set; would emit:\n{text}", file=sys.stderr, end="")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


# ── Driver ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="File holding `devcontainer outdated --output-format json` output; default stdin.",
    )
    args = parser.parse_args(argv)

    # Opened after parsing rather than via argparse.FileType, which is deprecated in 3.14.
    if args.json:
        with open(args.json, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.load(sys.stdin)

    features = payload.get("features", {})
    drift = find_drift(features)
    major = find_major_available(features)

    if drift:
        print("Lock drift (devcontainer upgrade will move these):")
        print("".join(f"  {n}: {c} -> {w}\n" for n, c, w in drift), end="")
    else:
        print("Lock is current: every feature is at the newest version within its pinned major.")
    if major:
        print("Newer major available (needs a devcontainer.json reference bump, not upgrade):")
        print("".join(f"  {n}: pinned {w}, latest {latest}\n" for n, w, latest in major), end="")

    _emit_github_output(format_outputs(drift, format_summary(drift, major)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
