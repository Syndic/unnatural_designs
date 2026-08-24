#!/usr/bin/env python3
"""
Verifies that every Python language-level declaration in the repo agrees with //:.python-version.

//:.python-version is the canonical pin. Four consumers cannot read it and carry their own copy;
this guard is what keeps those copies honest, because each one fails *silently* when it drifts --
a stale value is still a valid value, so every tool stays green while targeting the wrong level.

  - pyproject.toml `requires-python`  -- the floor ruff and ty both derive their target from.
    Neither is pinned separately: deleting `[tool.ruff] target-version` and
    `[tool.ty.environment] python-version` is what makes this the single home for the two.
  - MODULE.bazel `python_version`     -- rules_python's toolchain, once per call site.
  - .devcontainer/Dockerfile ARG      -- the image build never COPYs the workspace, so it
                                         cannot read the pin file at build time.
  - .github/workflows/*.yml           -- must read the file, never carry a literal.

The pin is deliberately `<major>.<minor>` with no patch. rules_python ships exactly one patch
per minor in its TOOL_VERSIONS table (2.3.2: 3.14 -> 3.14.4) while the version datasources
offer newer ones, so a three-segment pin plus Renovate is a scheduled build break. The three
resolvers therefore land on different patches of the same minor, by design.

Usage: ./meta/scripts/check_python_version.py
"""

import re
import sys
import tomllib
from pathlib import Path

# When invoked as `python3 meta/scripts/check_python_version.py` (the form used in CI and by
# pre-commit), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import col_range, workspace_root

PIN_FILE = ".python-version"

# No patch segment: see the module docstring for why a three-segment pin cannot work here.
_PIN_RE = re.compile(r"^\d+\.\d+$")

_MODULE_BAZEL_RE = re.compile(r'python_version\s*=\s*"([^"]+)"')
_DOCKERFILE_ARG_RE = re.compile(r"^ARG\s+PYTHON_VERSION=(\S+)")
_WORKFLOW_LITERAL_RE = re.compile(r"^\s*python-version:\s*(\S+)")
_WORKFLOW_FILE_RE = re.compile(r"^\s*python-version-file:\s*(\S+)")
_SETUP_PYTHON_RE = re.compile(r"^\s*-?\s*uses:\s*actions/setup-python@")


def _problem(rel: Path | str, lineno: int, cols: tuple[int, int], message: str) -> str:
    """`file:line:start-end: message`, the shape .vscode/tasks.json's problem matcher parses."""
    return f"{rel}:{lineno}:{cols[0]}-{cols[1]}: {message}"


def _locate(path: Path, needle: str) -> tuple[int, tuple[int, int]]:
    """1-based line number of `needle`'s first occurrence in `path`, plus its column range."""
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if needle in line:
            return lineno, col_range(path, lineno, needle)
    return 1, (1, 2)


def read_pin(root: Path) -> tuple[str | None, list[str]]:
    """The canonical level from //:.python-version, plus any problems with the file itself."""
    path = root / PIN_FILE
    if not path.is_file():
        return None, [
            _problem(PIN_FILE, 1, (1, 2), "missing: the canonical Python language level lives here")
        ]

    raw = path.read_text()
    version = raw.strip()
    if not _PIN_RE.match(version):
        return None, [
            _problem(
                PIN_FILE,
                1,
                (1, max(len(version), 1) + 1),
                f"expected a bare '<major>.<minor>' such as 3.14, got {version!r}",
            )
        ]

    problems = []
    # setup-python and uv both read this file verbatim; a stray second line or missing newline
    # is the kind of thing that works locally and fails on a runner.
    if raw != version + "\n":
        problems.append(
            _problem(PIN_FILE, 1, (1, len(version) + 1), "must be one line plus a trailing newline")
        )
    return version, problems


def check_pyproject(root: Path, version: str) -> list[str]:
    """`requires-python` must be the floor matching the pin -- ruff and ty read it as the target."""
    rel = Path("pyproject.toml")
    path = root / rel
    if not path.is_file():
        return []

    data = tomllib.loads(path.read_text())
    expected = f">={version}"
    actual = data.get("project", {}).get("requires-python")
    if actual == expected:
        return []

    lineno, cols = _locate(path, "requires-python")
    return [
        _problem(
            rel,
            lineno,
            cols,
            f"requires-python is {actual!r}, expected {expected!r} to match {PIN_FILE} "
            f"({version}); ruff and ty derive their target Python from this floor",
        )
    ]


def check_module_bazel(root: Path, version: str) -> list[str]:
    """Every rules_python `python_version` call site must equal the pin."""
    rel = Path("MODULE.bazel")
    path = root / rel
    if not path.is_file():
        return []

    problems = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        match = _MODULE_BAZEL_RE.search(line)
        if match and match.group(1) != version:
            problems.append(
                _problem(
                    rel,
                    lineno,
                    col_range(path, lineno, match.group(1)),
                    f"python_version is {match.group(1)!r}, expected {version!r} "
                    f"to match {PIN_FILE}",
                )
            )
    return problems


def check_dockerfile(root: Path, version: str) -> list[str]:
    """The devcontainer ARG must equal the pin; the image build cannot read the file itself."""
    rel = Path(".devcontainer/Dockerfile")
    path = root / rel
    if not path.is_file():
        return []

    problems = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        match = _DOCKERFILE_ARG_RE.match(line)
        if match and match.group(1) != version:
            problems.append(
                _problem(
                    rel,
                    lineno,
                    col_range(path, lineno, match.group(1)),
                    f"PYTHON_VERSION is {match.group(1)!r}, expected {version!r} "
                    f"to match {PIN_FILE}",
                )
            )
    return problems


def check_workflows(root: Path) -> list[str]:
    """Workflows must read the pin file and never carry a literal or point at pyproject.toml.

    Discovered by glob rather than hand-listed: a list would reproduce exactly the gap this
    guard exists to close, since a new workflow is precisely what nobody remembers to add.
    """
    workflow_dir = root / ".github/workflows"
    if not workflow_dir.is_dir():
        return []

    problems = []
    for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
        rel = path.relative_to(root)
        lines = path.read_text().splitlines()
        setup_steps = 0
        version_files = 0

        for lineno, line in enumerate(lines, start=1):
            if _SETUP_PYTHON_RE.match(line):
                setup_steps += 1

            literal = _WORKFLOW_LITERAL_RE.match(line)
            if literal:
                problems.append(
                    _problem(
                        rel,
                        lineno,
                        col_range(path, lineno, "python-version:"),
                        f"hardcoded python-version: {literal.group(1)}; use "
                        f"`python-version-file: {PIN_FILE}` so the level has one home",
                    )
                )

            version_file = _WORKFLOW_FILE_RE.match(line)
            if version_file:
                version_files += 1
                if version_file.group(1) != PIN_FILE:
                    problems.append(
                        _problem(
                            rel,
                            lineno,
                            col_range(path, lineno, version_file.group(1)),
                            f"reads {version_file.group(1)}, expected {PIN_FILE}; "
                            f"setup-python resolves pyproject.toml's requires-python as a "
                            f"semver *range* and installs the newest match",
                        )
                    )

        # A setup-python step with neither input silently takes the runner's default Python.
        if setup_steps > version_files:
            problems.append(
                _problem(
                    rel,
                    1,
                    (1, 2),
                    f"{setup_steps} setup-python step(s) but {version_files} "
                    f"`python-version-file:` -- every step must pin the level explicitly",
                )
            )

    return problems


def main() -> int:
    root = workspace_root()
    version, problems = read_pin(root)

    if version is not None:
        problems += check_pyproject(root, version)
        problems += check_module_bazel(root, version)
        problems += check_dockerfile(root, version)
    problems += check_workflows(root)

    for problem in problems:
        print(problem)

    if problems:
        count = len(problems)
        plural = "" if count == 1 else "s"
        print(f"\n{count} Python language-level problem{plural}.", file=sys.stderr)
        return 1

    print(f"Every Python language-level declaration matches {PIN_FILE} ({version}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
