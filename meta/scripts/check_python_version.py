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

# A whole-value reference to one matrix axis -- the only expression form this guard can resolve,
# and so the only one it accepts. Anything else is reported rather than waved through.
_MATRIX_REF_RE = re.compile(r"^\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}$")
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


def _literal_problem(
    value: str | None, matrix: dict[str, list[str]], version: str | None
) -> str | None:
    """What is wrong with a `python-version:` input, or None if nothing is.

    Split out because the expression case has three outcomes rather than two, and inlining them
    buried the ordinary hardcoded-literal path under the matrix bookkeeping.
    """
    if value is None:
        # A list or nested mapping. Still a level set inline; it just has no useful text form,
        # so describe the shape rather than rendering the None the parser reports.
        return (
            "hardcoded python-version (a list or mapping, not a version string); "
            f"use `python-version-file: {PIN_FILE}` so the level has one home"
        )

    reference = _MATRIX_REF_RE.match(value.strip())
    if reference is None:
        if _EXPRESSION_RE.search(value):
            # Some other expression -- `env.`, `inputs.`, a computed string. Reported rather than
            # allowed: an unreadable pin is otherwise indistinguishable from a correct one, and
            # the second reading is the one that passes.
            return (
                f"python-version is the expression {value}, which this guard cannot resolve; "
                f"use `python-version-file: {PIN_FILE}`, or a plain `${{{{ matrix.<axis> }}}}` "
                f"reference whose axis can be read"
            )
        return (
            f"hardcoded python-version: {value}; use `python-version-file: {PIN_FILE}` "
            f"so the level has one home"
        )

    axis = reference.group(1)
    values = matrix.get(axis)
    if values is None:
        return (
            f"python-version reads `matrix.{axis}`, which this job does not define as a plain "
            f"list of values, so the level it installs cannot be checked"
        )
    if version is not None and version not in values:
        # The axis is the level for this job. Testing a range wider than the pin is the point
        # (#272), but the pin itself must be in the range, or the job stops exercising the
        # version everything else in the repo targets the moment the pin moves.
        return (
            f"matrix.{axis} is {values}, which does not include {PIN_FILE}'s {version}; "
            f"a matrix may test more versions than the pin, never fewer"
        )
    return None


def check_workflows(root: Path, version: str | None = None) -> list[str]:
    """Every `setup-python` step must pin the level, and only via the pin file.

    Parsed, not matched. A line-oriented scan has no notion of step context, so it flags a
    `strategy.matrix` axis named `python-version`, a `${{ matrix.python-version }}` pass-through,
    and a `python-version:` line inside a `run: |` heredoc -- while missing a flow-style step
    (`- {uses: …, with: {python-version: "3.13"}}`) entirely, which is a literal that passes the
    guard. Structure is what tells those apart; see `_workflows.py` and #268.

    The only expression accepted is a whole-value `${{ matrix.<axis> }}` reference whose axis
    this job defines as a plain list, and that list must contain the pin. A job may test more
    versions than the pin (#272's shape, testing a member across its supported range) but never
    fewer -- otherwise the level simply moves into a list nothing verifies, and the job stops
    exercising the version the rest of the repo targets the moment the pin advances. Every other
    expression is reported: an unreadable pin is otherwise indistinguishable from a correct one.

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

        for uses, step_line, inputs, matrix in steps:
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
                cols = col_range(path, lineno, "python-version")
                message = _literal_problem(value, matrix, version)
                if message is not None:
                    problems.append(_problem(rel, lineno, cols, message))
            else:
                # Neither input. v7 does not fall back to the runner's Python: it looks for
                # `.python-version` in the *working directory* (`resolveVersionInputFromDefault
                # File`), so this usually resolves to the right level by accident. The lookup is
                # cwd-relative though, so a `working-directory:` or a checkout into a
                # subdirectory silently finds nothing and the step takes the runner default
                # instead. Naming the file is what makes that dependency visible at the step.
                problems.append(
                    _problem(
                        rel,
                        step_line,
                        (1, 2),
                        f"setup-python step names no version input; it would find {PIN_FILE} "
                        f"only by cwd lookup, which a `working-directory:` or a nested "
                        f"checkout breaks silently -- set `python-version-file: {PIN_FILE}`",
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
    # Passed through rather than gated on: the structural problems (a hardcoded literal, both
    # inputs, a wrong file) are worth reporting even when the pin file itself is unreadable.
    problems += check_workflows(root, version)

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
