#!/usr/bin/env python3
"""
Verifies that every Python language-level declaration in the repo agrees with //:.python-version.

//:.python-version is the canonical pin. Four consumers cannot read it and carry their own copy;
this guard is what keeps those copies honest, because each one fails *silently* when it drifts --
a stale value is still a valid value, so every tool stays green while targeting the wrong level.

  - pyproject.toml `requires-python`  -- the range ruff and ty both derive their target from.
    Neither is pinned separately: deleting `[tool.ruff] target-version` and
    `[tool.ty.environment] python-version` is what makes this the single home for the two.
  - MODULE.bazel `python_version`     -- rules_python's toolchain, once per call site.
  - .devcontainer/Dockerfile ARG      -- the image build never COPYs the workspace, so it
                                         cannot read the pin file at build time.
  - setup-python steps                -- in .github/workflows/ *and* .github/actions/; must
                                         read the file, never carry a literal.

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

from meta.scripts._workflows import action_steps
from meta.scripts._workspace import col_range, workspace_root

PIN_FILE = ".python-version"

# No patch segment: see the module docstring for why a three-segment pin cannot work here.
_PIN_RE = re.compile(r"^\d+\.\d+$")

# Starlark and Dockerfile are not YAML; a line-anchored pattern is the right tool for both, and
# neither has the nesting that makes a scan wrong for workflows.
_MODULE_BAZEL_RE = re.compile(r'python_version\s*=\s*"([^"]+)"')
_DOCKERFILE_ARG_RE = re.compile(r"^ARG\s+PYTHON_VERSION=(\S+)")

_SETUP_PYTHON = "actions/setup-python"

# `${{ … }}` anywhere in the value: a matrix pass-through or other expression is parameterised,
# not hardcoded, so it is not this guard's business.
_EXPRESSION_RE = re.compile(r"\$\{\{.*\}\}")


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
    """`requires-python` must pin the pin's minor -- ruff and ty read it as their target.

    Exact-minor (`==3.14.*`), not a `>=` floor. A floor admits the next minor silently, and
    Renovate's `replace` strategy only rewrites a range the new version falls *outside*, so a
    floor is never bumped at all. This applies to the workspace root, which is `package = false`.
    A published member must declare its real support range instead: capping `requires-python`
    is contagious, and resolvers react to an unsatisfiable cap by silently installing an older
    release rather than erroring.
    """
    rel = Path("pyproject.toml")
    path = root / rel
    if not path.is_file():
        return []

    data = tomllib.loads(path.read_text())
    expected = f"=={version}.*"
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
            f"({version}); ruff and ty derive their target Python from it, and only an "
            f"exact-minor range is one Renovate will bump",
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


def action_yaml_files(root: Path) -> list[Path]:
    """Every workflow and composite action in the repo.

    Both trees, because a step is a step wherever it lives: a `setup-python` added to a composite
    action under `.github/actions/` pins the level exactly as a workflow step does, and omitting
    that directory would be a hand-maintained blind spot in a guard whose whole claim is that it
    has none. Latent today — no composite action uses setup-python — which is when it is cheap.
    """
    found = []
    for directory, pattern in (
        (root / ".github/workflows", "*.y*ml"),
        (root / ".github/actions", "**/action.y*ml"),
    ):
        if directory.is_dir():
            found.extend(p for p in directory.glob(pattern) if p.suffix in (".yml", ".yaml"))
    return sorted(found)


def check_workflows(root: Path) -> list[str]:
    """Every `setup-python` step must pin the level, and only via the pin file.

    Parsed, not matched. A line-oriented scan has no notion of step context, so it flags a
    `strategy.matrix` axis named `python-version`, a `${{ matrix.python-version }}` pass-through,
    and a `python-version:` line inside a `run: |` heredoc -- while missing a flow-style step
    (`- {uses: …, with: {python-version: "3.13"}}`) entirely, which is a literal that passes the
    guard. Structure is what tells those apart; see `_workflows.py` and #268.

    A `${{ }}` expression is deliberately allowed: a job that drives `setup-python` from a matrix
    axis is parameterised rather than hardcoded, and that is the shape #272 needs for testing a
    member across its supported range. Note this moves the level into the matrix list, which
    nothing here checks -- a known gap, not a claim of safety.

    A step carrying *both* inputs is rejected outright rather than resolved. setup-python prefers
    `python-version` and ignores the file (warning only, in the run log), so the file reads as the
    pin source while doing nothing; and `getMultilineInput` treats an empty expression as absent,
    so the same YAML silently switches back to the file if the axis is ever renamed away. Refusing
    the ambiguity is also what keeps this function from having to mirror the action's precedence.
    """
    problems = []
    for path in action_yaml_files(root):
        rel = path.relative_to(root)
        steps, parse_problems = action_steps(path)

        for lineno, message in sorted(parse_problems.items()):
            problems.append(_problem(rel, lineno, (1, 2), message))

        for uses, step_line, inputs in steps:
            if uses is None or not uses.startswith(f"{_SETUP_PYTHON}@"):
                continue

            version_file = inputs.get("python-version-file")
            literal = inputs.get("python-version")

            if version_file is not None and literal is not None:
                _, lineno = literal
                problems.append(
                    _problem(
                        rel,
                        lineno,
                        col_range(path, lineno, "python-version"),
                        "both python-version and python-version-file are set; setup-python "
                        f"uses python-version and ignores the file, so `{PIN_FILE}` is not "
                        "actually read here -- drop one",
                    )
                )
            elif version_file is not None:
                value, lineno = version_file
                if value != PIN_FILE:
                    problems.append(
                        _problem(
                            rel,
                            lineno,
                            col_range(path, lineno, "python-version-file"),
                            f"reads {value}, expected {PIN_FILE}; setup-python resolves "
                            f"pyproject.toml's requires-python as a semver *range* and "
                            f"installs the newest match",
                        )
                    )
            elif literal is not None:
                value, lineno = literal
                if value is None or not _EXPRESSION_RE.search(value):
                    # A non-scalar value (a list, a nested mapping) has no useful text form, so
                    # describe the shape instead of rendering the None the parser reports.
                    shown = (
                        f": {value}"
                        if value is not None
                        else " (a list or mapping, not a version string)"
                    )
                    problems.append(
                        _problem(
                            rel,
                            lineno,
                            col_range(path, lineno, "python-version"),
                            f"hardcoded python-version{shown}; use "
                            f"`python-version-file: {PIN_FILE}` so the level has one home",
                        )
                    )
            else:
                # Neither input: the step silently takes the runner image's default Python.
                problems.append(
                    _problem(
                        rel,
                        step_line,
                        (1, 2),
                        f"setup-python step pins nothing; add `python-version-file: {PIN_FILE}`",
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
