#!/usr/bin/env python3
"""
Shellchecks every composite action's shell `run:` steps, by handing each one to actionlint.

actionlint runs shellcheck over workflows' `run:` blocks but reads only `.github/workflows/`, and
the shellcheck job reads only tracked `*.sh` files, so a script written inline in an action's
`run:` falls between them. This wraps each such step in a one-step workflow and runs the pinned
actionlint over it, keeping only its shellcheck findings. The preamble, the `${{ }}` masking and
the excluded codes are therefore actionlint's own, and move with its pin.

actionlint's other rules are dropped: the wrapper carries none of the action's `inputs` or step
context, so its expression checks would report noise.

A step whose `shell:` is a language rather than a shell is not linted. One naming anything else,
or no shell at all, is reported rather than skipped, as is finding no step to lint. The shellcheck
and actionlint on PATH are checked against the Dockerfile's pins first: actionlint resolves
shellcheck from PATH, where an earlier setup step can put some other copy ahead of the pinned one.

Usage: ./meta/scripts/check_composite_action_scripts.py (needs the pinned tools on PATH)
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import yaml

# When invoked as `python3 meta/scripts/check_composite_action_scripts.py` (the form CI uses), the
# workspace root is not on sys.path, so `from meta.scripts.X` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workflows import RunStep, composite_action_files, run_steps
from meta.scripts._workspace import exit_status, workspace_root

# Matched on the shell's first word, so a custom `bash -e {0}` still lints as bash.
_LINTED = frozenset({"bash", "sh"})
# Shells that run something other than shell script, so shellcheck has nothing to say about them.
_NOT_SHELL = frozenset({"cmd", "powershell", "pwsh", "python"})

_EXPRESSION_RE = re.compile(r"\$\{\{.*?\}\}", re.S)
# How actionlint words a shellcheck finding; its line and column are relative to the script.
_FINDING_RE = re.compile(
    r"^shellcheck reported issue in this script: "
    r"SC(?P<code>\d+):(?P<level>\w+):(?P<line>\d+):(?P<column>\d+): (?P<message>.*)$",
    re.S,
)


class Script(NamedTuple):
    """One shell `run:` step, located well enough to put a finding back on its source line.

    `first_line` is the 1-based file line holding the body's first line, and `column` the column
    that line's text starts at, so a body (line, column) maps back by plain addition. Where the
    scalar's style breaks that correspondence, `exact` is False and findings are reported at the
    `run:` key, carrying the body position in their message instead.
    """

    run_line: int
    first_line: int
    column: int
    exact: bool
    shell: str
    body: str


def _locate(value: yaml.ScalarNode, lines: list[str]) -> tuple[int, int, bool]:
    """`(first_line, column, exact)` for a `run:` value; see Script."""
    mark = value.start_mark
    # Optional in the stubs, always set by yaml.compose.
    if mark is None:
        raise ValueError("yaml.compose returned a node with no source position")
    if value.style == "|":
        # The body starts on the line after the `|` indicator, at the block's indentation.
        body_lines = lines[mark.line + 1 :]
        indent = next((len(ln) - len(ln.lstrip(" ")) for ln in body_lines if ln.strip()), 0)
        return mark.line + 2, indent + 1, True
    if "\n" not in value.value:
        # A plain scalar starts at the mark, a quoted one a character later — and is exact only
        # when no escape sits between the source and its value.
        start = mark.column + (0 if value.style is None else 1)
        if lines[mark.line][start : start + len(value.value)] == value.value:
            return mark.line + 1, start + 1, True
    return mark.line + 1, 1, False


def _unlintable(step: RunStep) -> str | None:
    """Why a step cannot be linted, "skip" for a non-shell language, or None if it can."""
    dialect = (step.shell or "").split()[:1]
    if dialect and dialect[0] in _NOT_SHELL:
        return "skip"
    if not dialect or dialect[0] not in _LINTED:
        return (
            f"cannot lint a `run:` step with shell {step.shell!r}; expected one of "
            f"{sorted(_LINTED)} or a non-shell {sorted(_NOT_SHELL)}"
        )
    return None


def scripts(action_file: Path) -> tuple[list[Script], list[tuple[int, str]]]:
    """The action's lintable shell steps, plus `(line, problem)` for any step that is not."""
    steps, parse_problems = run_steps(action_file)
    problems = list(parse_problems.items())
    lines = action_file.read_text(encoding="utf-8").splitlines()
    found: list[Script] = []
    for step in steps:
        problem = _unlintable(step)
        if problem == "skip":
            continue
        run_line = step.line
        if problem is not None or not isinstance(step.value, yaml.ScalarNode):
            problems.append((run_line, problem or "`run:` is not a plain string"))
            continue
        first, column, exact = _locate(step.value, lines)
        shell = (step.shell or "").split()[0]
        found.append(Script(run_line, first, column, exact, shell, step.value.value))
    return found, problems


def body_position(body: str, line: int, column: int) -> tuple[int, int]:
    """Where actionlint's script `(line, column)` falls in `body`.

    actionlint replaces every byte of each `${{ }}` with `_`, newlines included, before shellcheck
    reads the script, so an expression spanning lines or holding non-ASCII moves every position
    after it. A position inside an expression maps to the expression's start, and one this cannot
    place is returned unchanged.
    """
    masked: list[str] = []
    origin: list[int] = []  # body offset of each masked character
    pos = 0
    for match in _EXPRESSION_RE.finditer(body):
        masked.append(body[pos : match.start()])
        origin.extend(range(pos, match.start()))
        width = len(match.group().encode())
        masked.append("_" * width)
        origin.extend([match.start()] * width)
        pos = match.end()
    masked.append(body[pos:])
    origin.extend(range(pos, len(body)))

    masked_lines = "".join(masked).split("\n")
    if not 1 <= line <= len(masked_lines):
        return line, column
    offset = sum(len(ln) + 1 for ln in masked_lines[: line - 1]) + column - 1
    if not 0 <= offset < len(origin):
        return line, column
    target = origin[offset]
    return body.count("\n", 0, target) + 1, target - body.rfind("\n", 0, target)


def wrapper_workflow(script: Script) -> str:
    """A one-step workflow running `script` the way the action does, for actionlint to read."""
    step = {"shell": script.shell, "run": script.body}
    jobs = {"lint": {"runs-on": "ubuntu-latest", "steps": [step]}}
    return yaml.safe_dump({"on": "push", "jobs": jobs}, sort_keys=False)


def format_findings(script: Script, rel: Path, errors: list[dict]) -> list[str]:
    """`file:line:start-end: SCnnnn (level): message` for each shellcheck finding actionlint made.

    The shape .vscode/tasks.json's problem matcher parses, as the repo's other checks print.
    """
    found = []
    for error in errors:
        if error.get("kind") != "shellcheck":
            continue
        match = _FINDING_RE.match(error["message"])
        if match is None:
            # A wording this cannot place is still a finding, not something to drop.
            found.append(f"{rel}:{script.run_line}:1-2: {error['message']}")
            continue
        line, column = body_position(script.body, int(match["line"]), int(match["column"]))
        text = f"SC{match['code']} ({match['level']}): {match['message']}"
        if script.exact:
            start = column + script.column - 1
            found.append(f"{rel}:{script.first_line + line - 1}:{start}-{start + 1}: {text}")
        else:
            found.append(
                f"{rel}:{script.run_line}:1-2: {text} (`run:` line {line}, column {column})"
            )
    return found


def lint(script: Script, rel: Path) -> list[str]:
    result = subprocess.run(
        ["actionlint", "-pyflakes=", "-format", "{{json .}}", "-stdin-filename", str(rel), "-"],
        input=wrapper_workflow(script),
        capture_output=True,
        text=True,
    )
    # 0 is clean and 1 is findings (of any rule); anything else is actionlint itself failing.
    if result.returncode not in (0, 1):
        return [f"{rel}:{script.run_line}:1-2: actionlint failed: {result.stderr.strip()}"]
    return format_findings(script, rel, json.loads(result.stdout or "[]"))


class Pin(NamedTuple):
    """A Dockerfile `<TOOL>_VERSION`, without its leading `v`, and where it sits."""

    version: str
    line: int
    end_column: int


def pinned_version(dockerfile: str, tool: str) -> Pin | None:
    match = re.search(rf"^ARG {tool.upper()}_VERSION=v?(\S+)$", dockerfile, re.M)
    if match is None:
        return None
    return Pin(match.group(1), dockerfile.count("\n", 0, match.start()) + 1, len(match[0]) + 1)


def reported_version(output: str) -> str | None:
    """The version `--version` prints: shellcheck's `version: X`, actionlint's bare first line."""
    match = re.search(r"^(?:version: )?v?(\d+\.\d+\.\d+)$", output, re.M)
    return match.group(1) if match else None


def tool_problems(root: Path) -> list[str]:
    """Each pinned tool that is not the one on PATH, reported against the Dockerfile's pin."""
    dockerfile = (root / ".devcontainer" / "Dockerfile").read_text(encoding="utf-8")
    problems = []
    for tool in ("shellcheck", "actionlint"):
        pin = pinned_version(dockerfile, tool)
        try:
            output = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout
        except FileNotFoundError:
            output = ""
        on_path = reported_version(output)
        if pin is None or on_path != pin.version:
            where = f"{pin.line}:1-{pin.end_column}" if pin else "1:1-2"
            problems.append(
                f".devcontainer/Dockerfile:{where}: {tool} on PATH is {on_path or 'missing'}, "
                f"but the pin is {pin.version if pin else 'missing'}"
            )
    return problems


def check(root: Path) -> list[str]:
    problems = tool_problems(root)
    if problems:
        return problems
    linted = 0
    for action in composite_action_files(root):
        rel = action.relative_to(root)
        found, unlintable = scripts(action)
        problems += [f"{rel}:{line}:1-2: {message}" for line, message in unlintable]
        for script in found:
            problems += lint(script, rel)
            linted += 1
    if linted == 0:
        problems.append(".github/actions:1:1-2: no shell `run:` step found; nothing was linted")
    return problems


def main() -> int:
    problems = check(workspace_root())
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} problem(s) in composite action scripts.", file=sys.stderr)
    else:
        print("shellcheck is clean over every composite action's shell run: steps.")
    return exit_status(len(problems))


if __name__ == "__main__":
    sys.exit(main())
