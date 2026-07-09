#!/usr/bin/env python3
"""Ratify Renovate's Python dep-bump proposals via uv.

Renovate's pep621 manager cannot run `uv lock` on Mend-hosted Renovate (gated by
allowedUnsafeExecutions), so it falls back to the pip_requirements manager which edits
`requirements_lock.txt` in place. That edit alone is not authoritative — `uv.lock` is the
resolution source of truth.

This script treats Renovate's edits as upgrade signals and runs uv to either ratify them
or surface a constraint conflict:

  1. Extract proposed (name, target) pairs from the diff against the PR base.
  2. Run `uv lock --upgrade-package <name>` for each proposed bump. uv re-resolves and
     picks whatever version satisfies the workspace's constraints.
  3. Re-export `requirements_lock.txt` from the refreshed `uv.lock`.
  4. For each proposed package, check whether uv resolved to >= the target.
     - Honored for all: emit an empty `conflicts` step output. The caller workflow
       then commits both files.
     - Honored for none / some: emit a per-package diagnostic to the `conflicts` step
       output. The caller workflow files a REQUEST_CHANGES review.

I/O contract:
  - Reads BASE_REF from env (the base ref name, typically `main`).
  - Reads GITHUB_OUTPUT from env (the output file path GitHub Actions provides).
  - Runs `git diff origin/{BASE_REF} -- <requirements-lock>` to discover proposals.
  - Runs `uv lock ...` and `uv export ...` as side effects on the working tree.
  - Writes the `conflicts` step output as a multiline value (GitHub Actions heredoc
    syntax — `conflicts<<EOF\\n...\\nEOF\\n`).
  - Exits 0 on script success regardless of conflict status; exits non-zero only on
    subprocess / I/O failures the caller workflow should surface.

Pure functions (`extract_proposals`, `parse_resolved`, `find_conflicts`) carry all the
non-I/O logic so the test suite can exercise the moving parts without git or uv.

See docs/future-considerations.md "Auto-commit GitHub App" for the broader flow and
.github/workflows/renovate-derived-files.yml for the caller workflow.

Usage: python3 meta/scripts/ratify_renovate_proposals.py
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import subprocess
import sys
from pathlib import Path

# When invoked as `python3 meta/scripts/ratify_renovate_proposals.py` (the form used in
# the workflow), the workspace root is not on sys.path, so `from meta.scripts.X` would
# fail. Adding the workspace root explicitly fixes that and is harmless under bazel
# py_binary, where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packaging.version import Version

# Match a requirements.txt pin line: `name==version` (optionally followed by ` \` for
# multi-hash continuations). Captures (name, version). Used by both the diff parser and
# the resolved-version parser — keep them in sync via the shared regex.
_PIN_RE = re.compile(r"^([a-zA-Z0-9_.-]+)==([^ \\]+)")


# ── Domain types ──────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Proposal:
    """One package version Renovate proposed bumping in requirements_lock.txt.

    `name` is the canonical package name as it appears in the lockfile.
    `target` is the version Renovate wrote — what we'll compare uv's resolution against.
    """

    name: str
    target: str


# ── Pure functions (the part the tests exercise) ──────────────────────────────


def extract_proposals(diff_text: str) -> list[Proposal]:
    """Parse a unified diff over requirements_lock.txt; return Renovate's proposals.

    Walks the diff line by line, keeping `+`-prefixed lines that match `_PIN_RE`. Skips
    the `+++ b/file` header (the `++` prefix means the line after the `+` does not start
    with a package-name character), `+    --hash=sha256:...` continuations (start with
    whitespace), and `+    # via ...` comments (same reason). The result is sorted by
    name so the script's downstream behavior is deterministic.
    """
    proposals: dict[str, str] = {}
    for raw in diff_text.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        m = _PIN_RE.match(raw[1:])
        if m:
            proposals[m.group(1)] = m.group(2)
    return [Proposal(name=n, target=t) for n, t in sorted(proposals.items())]


def parse_resolved(lock_text: str) -> dict[str, str]:
    """Parse requirements.txt content into a {name: version} map.

    Only top-level `name==version` lines count; comments and continuation lines are
    skipped. Same regex as `extract_proposals` so the two views of the file always agree
    about what is "a package pin".
    """
    resolved: dict[str, str] = {}
    for raw in lock_text.splitlines():
        m = _PIN_RE.match(raw)
        if m:
            resolved[m.group(1)] = m.group(2)
    return resolved


def find_conflicts(proposals: list[Proposal], resolved: dict[str, str]) -> list[str]:
    """Per proposal, return a diagnostic string if uv did not honor the proposed bump.

    Honored means: uv's resolved version is >= the proposed version. uv may pick a
    newer version than Renovate proposed if one shipped between Renovate's scan and our
    `uv lock` — still counts as advancing the package in the spirit of the proposal.

    Two failure modes:
      - Package missing from the regenerated lockfile (uv removed the dep entirely).
      - Resolved version < proposed version (a workspace constraint forbids the bump).

    Empty list ⇒ all proposals honored, caller workflow commits the regenerated files.
    Non-empty list ⇒ caller workflow files a REQUEST_CHANGES review with these strings.
    """
    conflicts: list[str] = []
    for proposal in proposals:
        if proposal.name not in resolved:
            conflicts.append(
                f"{proposal.name}: not present in regenerated lockfile "
                f"(Renovate proposed {proposal.target})"
            )
            continue
        if Version(resolved[proposal.name]) < Version(proposal.target):
            conflicts.append(
                f"{proposal.name}: uv resolved {resolved[proposal.name]}; "
                f"Renovate proposed {proposal.target} (uv would not advance)"
            )
    return conflicts


# ── I/O wrappers (thin shells around subprocess + file I/O) ───────────────────


def _git_diff_base(base_ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "diff", f"origin/{base_ref}", "--", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _uv_lock(packages_to_upgrade: list[str]) -> None:
    """Refresh uv.lock against pyproject.toml, optionally with --upgrade-package flags.

    Empty `packages_to_upgrade` ⇒ plain `uv lock` (re-resolves against the current
    pyproject.toml without forcing any upgrades — the right behavior when Renovate
    edited only pyproject.toml and not requirements_lock.txt).
    """
    flags: list[str] = []
    for pkg in packages_to_upgrade:
        flags += ["--upgrade-package", pkg]
    subprocess.run(["uv", "lock", *flags], check=True)


def _uv_export(output_path: str) -> None:
    subprocess.run(
        [
            "uv",
            "export",
            "--format",
            "requirements-txt",
            "--no-emit-project",
            "--output-file",
            output_path,
        ],
        check=True,
    )


def _emit_github_output(name: str, lines: list[str]) -> None:
    """Append a multiline step output using GitHub Actions' heredoc syntax.

    No-ops if GITHUB_OUTPUT is unset (local invocation outside Actions); prints to
    stderr instead so the dev sees what would have been emitted.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(
            f"GITHUB_OUTPUT not set; would emit step output `{name}`:\n  "
            + "\n  ".join(lines or ["(empty)"]),
            file=sys.stderr,
        )
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}<<EOF\n")
        for line in lines:
            f.write(line + "\n")
        f.write("EOF\n")


# ── Driver ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements-lock",
        default="requirements_lock.txt",
        help="Path to the requirements_lock.txt file (default: cwd-relative).",
    )
    args = parser.parse_args(argv)

    base_ref = os.environ["BASE_REF"]
    lock_path = Path(args.requirements_lock)

    # 1. Discover proposed bumps from Renovate's diff against the PR base.
    diff = _git_diff_base(base_ref, str(lock_path))
    proposals = extract_proposals(diff)
    if proposals:
        print("Proposals: " + ", ".join(f"{p.name}=={p.target}" for p in proposals))
    else:
        print("No proposals discovered in requirements_lock.txt diff.")

    # 2. Refresh uv.lock honoring the proposals (or plain `uv lock` if none).
    _uv_lock([p.name for p in proposals])

    # 3. Re-export requirements_lock.txt from the refreshed uv.lock.
    _uv_export(str(lock_path))

    # 4. Check whether each proposal was honored by uv's resolution.
    resolved = parse_resolved(lock_path.read_text(encoding="utf-8"))
    conflicts = find_conflicts(proposals, resolved)
    if conflicts:
        print("Conflicts (uv refused to advance):")
        for line in conflicts:
            print(f"  {line}")
    else:
        print("All proposals honored.")

    # 5. Emit the conflicts step output; caller workflow branches on it.
    _emit_github_output("conflicts", conflicts)

    return 0


if __name__ == "__main__":
    sys.exit(main())
