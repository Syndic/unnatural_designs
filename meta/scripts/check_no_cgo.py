#!/usr/bin/env python3
"""
Enforces the monorepo's pure-Go policy: no `import "C"` in our Go source, and no transitive
Go dependency that compiles C, C++, cgo, SWIG, or ships pre-built .syso objects.

The build infrastructure (.bazelrc sets `--@rules_go//go/config:pure`) assumes pure Go so
that cross-compile to any GOOS/GOARCH works from any host without an LLVM toolchain or
sysroot. Introducing cgo invalidates that assumption — see
docs/future-considerations.md ("Introducing cgo or Python C-Extensions") for what would
need to change.

When this check fails:
  - If the cgo is in our code: remove it. If you genuinely need it, get explicit team
    agreement and rebuild the cross-compile infrastructure first.
  - If the cgo is in a transitive dep: pick a pure-Go alternative if one exists, or
    explicitly accept the cost and rebuild the cross-compile infrastructure first.

Module discovery delegates to check_go_work.registered_modules — go.work is the canonical
list, and check_go_work independently verifies that every on-disk go.mod is listed there,
so trusting the workspace file here is safe.

Usage: ./meta/scripts/check_no_cgo.py
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# When invoked as `python3 meta/scripts/check_no_cgo.py` (the form used in CI and by
# pre-commit), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
# Adding the workspace root explicitly fixes that and is harmless under bazel py_binary,
# where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.check_go_work import registered_modules  # noqa: E402

# Matches `import "C"` as a top-level statement (not inside a string or comment block).
# cgo's preamble is a comment block above the import, but the `import "C"` line itself is
# real code — it always matches this pattern when cgo is in use.
_CGO_IMPORT_RE = re.compile(r'^\s*import\s+"C"\s*$', re.MULTILINE)

# Directories we should not descend into.
_SKIP_DIR_PREFIXES = ("bazel-",)
_SKIP_DIR_NAMES = {".git", "node_modules"}


def workspace_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def find_cgo_in_sources(root: Path) -> list[Path]:
    """Return .go files containing `import "C"`, relative to root.

    bazel-* output dirs and .git are excluded.
    """
    offenders: list[Path] = []
    for go_file in root.rglob("*.go"):
        if any(
            part in _SKIP_DIR_NAMES or any(part.startswith(p) for p in _SKIP_DIR_PREFIXES)
            for part in go_file.parts
        ):
            continue
        try:
            content = go_file.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if _CGO_IMPORT_RE.search(content):
            offenders.append(go_file.relative_to(root))
    return offenders


def find_cgo_in_deps(module_dir: Path) -> list[str]:
    """Return import paths of third-party dependencies that compile native code.

    Runs `go list -deps` with CGO_ENABLED=1 so conditionally-compiled cgo packages are
    surfaced. Returns import paths whose package metadata declares non-empty CgoFiles,
    CFiles, CXXFiles, SwigFiles, or SysoFiles.

    Stdlib packages (.Standard == true) are excluded — they routinely contain cgo files
    behind build tags (runtime/cgo, net's system DNS resolver, os/user, syscall) that are
    only compiled in when CGO_ENABLED=1 *and* something imports "C". Since our build runs
    in pure mode (--@rules_go//go/config:pure), those paths are dead. The policy is about
    our code and our third-party deps; the stdlib is not actionable.
    """
    fmt = (
        "{{if not .Standard}}"
        "{{if or .CgoFiles .CFiles .CXXFiles .SwigFiles .SysoFiles}}"
        "{{.ImportPath}}"
        "{{end}}"
        "{{end}}"
    )
    env = {**os.environ, "CGO_ENABLED": "1"}
    result = subprocess.run(
        ["go", "list", "-deps", "-f", fmt, "./..."],
        cwd=module_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`go list` failed in {module_dir}:\n{result.stderr.strip()}"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def check(root: Path) -> int:
    exit_code = 0

    sources = find_cgo_in_sources(root)
    if sources:
        exit_code = 1
        print("Found `import \"C\"` in repo source files:")
        for path in sources:
            print(f"  {path}")
        print()

    modules = sorted(registered_modules(root))
    if not shutil.which("go"):
        print(
            "warning: `go` is not on PATH; skipping transitive dependency check.\n"
            "         Run with Go installed to fully validate.",
            file=sys.stderr,
        )
    else:
        for module_rel in modules:
            try:
                offenders = find_cgo_in_deps(root / module_rel)
            except RuntimeError as e:
                print(f"error: {e}", file=sys.stderr)
                exit_code = 1
                continue
            if offenders:
                exit_code = 1
                print(
                    f"Module //{module_rel} has dependencies that compile C/C++/cgo/SWIG:"
                )
                for path in offenders:
                    print(f"  {path}")
                print()

    if exit_code == 0:
        scope = f"{len(modules)} Go module(s)" if modules else "no Go modules"
        print(f"OK — pure-Go policy satisfied across {scope}.")
    else:
        print(
            "Pure-Go policy violation. See docs/future-considerations.md "
            "(\"Introducing cgo or Python C-Extensions\") for the implications "
            "and required infrastructure changes."
        )

    return exit_code


def main() -> int:
    return check(workspace_root())


if __name__ == "__main__":
    sys.exit(main())
