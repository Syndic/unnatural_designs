#!/usr/bin/env python3
"""Enumerate the Go workspace's tidy targets and the derived files a tidy/sync can rewrite.

Used by renovate-derived-files.yml after Renovate bumps a go.mod: the workflow runs
`go mod tidy` in each module (``--what modules``) and hands the commit step the files
`go mod tidy` + `go work sync` may have rewritten (``--what files``). Both lists derive from
go.work's `use` directives (registered_modules) — the same source check_go_work.py holds
authoritative — so a module added to the workspace is picked up here without a second edit.

go.sum and go.work.sum are DERIVED (Renovate's `go get` leaves them stale and Mend-hosted
Renovate can't regenerate them — see the workflow header). go.mod is in the commit list too
because `go mod tidy` can prune the indirect block that `go get` left behind.

Usage:
  python3 meta/scripts/go_derived_files.py --what {modules,files}
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

# Same sys.path shim as the sibling check_*.py scripts: when invoked as
# `python3 meta/scripts/go_derived_files.py` the workspace root is not importable, so add it.
# Harmless under bazel py_binary, where rules_python already resolves the import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import registered_modules, workspace_root


def module_dirs(modules: Iterable[Path]) -> list[str]:
    """Sorted, workspace-relative module directories — where `go mod tidy` runs."""
    return sorted(m.as_posix() for m in modules)


def derived_files(modules: Iterable[Path]) -> list[str]:
    """Derived Go files a tidy+sync may rewrite: each module's go.mod/go.sum, then go.work.sum.

    Modules are sorted; paths are workspace-relative POSIX strings, matching what the commit
    action and `git diff` expect. go.work.sum is the workspace-level artifact of `go work sync`
    and always comes last — present even with zero members, since a sync can still touch it.
    """
    files: list[str] = []
    for m in sorted(modules, key=lambda p: p.as_posix()):
        files.append(f"{m.as_posix()}/go.mod")
        files.append(f"{m.as_posix()}/go.sum")
    files.append("go.work.sum")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--what", required=True, choices=("modules", "files"))
    args = parser.parse_args(argv)

    modules = registered_modules(workspace_root()).keys()
    lines = module_dirs(modules) if args.what == "modules" else derived_files(modules)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
