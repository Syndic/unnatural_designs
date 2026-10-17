#!/usr/bin/env python3
"""
Runs shellcheck over every composite action's shell `run:` steps.

actionlint lints the `run:` blocks of `.github/workflows/` only, and the shellcheck job lints
tracked `*.sh` files only, so a script written inline in `.github/actions/*/action.yml` falls
between them. This feeds each such step to shellcheck the way actionlint feeds a workflow's, so a
script reaches the same verdict wherever it lives:

  - the body is prefixed with the `set -e` GitHub's own wrapper applies for that `shell:`, and
    each `${{ }}` expression is replaced by underscores of the same length, keeping columns;
  - shellcheck gets actionlint's flags, including its `--norc` and its list of excluded codes.

A step whose `shell:` is a language rather than a shell (python, pwsh) is not this check's to lint.
One naming anything else is reported rather than skipped, since an unrecognised shell is the case a
silent skip would hide.

Usage: ./meta/scripts/check_composite_action_scripts.py (needs the pinned shellcheck on PATH)
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

from meta.scripts._workflows import action_yaml_files
from meta.scripts._workspace import workspace_root

# What actionlint passes, captured from its own invocation; format aside, a change here moves the
# verdict away from the one workflow scripts get.
_EXCLUDED = "SC1091,SC2194,SC2050,SC2153,SC2154,SC2157,SC2043"

# The preamble GitHub's runner wraps each shell with (`bash --noprofile --norc -eo pipefail {0}`,
# `sh -e {0}`), keyed by the shell's first word so a custom `bash -e {0}` still lints as bash.
_PREAMBLES = {"bash": "set -eo pipefail", "sh": "set -e"}

# Shells that run something other than shell script, so shellcheck has nothing to say about them.
_NOT_SHELL = frozenset({"cmd", "powershell", "pwsh", "python"})

_EXPRESSION_RE = re.compile(r"\$\{\{.*?\}\}", re.S)


class RunScript(NamedTuple):
    """One shell `run:` step, located well enough to put a finding back on its source line.

    `first_line` is the 1-based file line holding the body's first line, and `column` the column
    that line's text starts at, so shellcheck's (line, column) maps back by plain addition. Where
    the scalar's style breaks that correspondence (folded, or a multi-line plain/quoted value),
    `exact` is False and findings are reported at the `run:` key instead.
    """

    run_line: int
    first_line: int
    column: int
    exact: bool
    shell: str
    body: str


def mask_expressions(body: str) -> str:
    """`body` with each `${{ }}` replaced by underscores, newlines kept, so positions survive."""
    return _EXPRESSION_RE.sub(lambda m: re.sub(r"[^\n]", "_", m.group()), body)


def _pairs(node: yaml.Node | None) -> dict[str, tuple[yaml.Node, yaml.Node]]:
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {k.value: (k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode)}


def _locate(value: yaml.ScalarNode, lines: list[str]) -> tuple[int, int, bool]:
    """`(first_line, column, exact)` for a `run:` value; see RunScript."""
    mark = value.start_mark
    # Optional in the stubs, always set by yaml.compose.
    if mark is None:
        raise ValueError("yaml.compose returned a node with no source position")
    if value.style == "|":
        # The body starts on the line after the `|` indicator, at the block's indentation.
        indent = next(
            (len(ln) - len(ln.lstrip(" ")) for ln in lines[mark.line + 1 :] if ln.strip()), 0
        )
        return mark.line + 2, indent + 1, True
    if value.style is None and "\n" not in value.value:
        return mark.line + 1, mark.column + 1, True
    return mark.line + 1, 1, False


def run_scripts(text: str) -> tuple[list[RunScript], list[tuple[int, str]]]:
    """Every shell `run:` step in a composite action's text, plus `(line, problem)` pairs for any
    step that cannot be linted.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        return [], [(mark.line + 1 if mark else 1, f"could not be parsed as YAML ({exc.args[0]})")]

    runs = _pairs(root).get("runs")
    steps = _pairs(runs[1] if runs else None).get("steps")
    if steps is None or not isinstance(steps[1], yaml.SequenceNode):
        return [], []

    lines = text.splitlines()
    scripts: list[RunScript] = []
    problems: list[tuple[int, str]] = []
    for step in steps[1].value:
        fields = _pairs(step)
        if "run" not in fields:
            continue
        key, value = fields["run"]
        run_line = key.start_mark.line + 1
        shell = fields["shell"][1].value if "shell" in fields else ""
        dialect = (shell.split() or [""])[0]
        if dialect in _NOT_SHELL:
            continue
        if dialect not in _PREAMBLES or not isinstance(value, yaml.ScalarNode):
            problems.append(
                (
                    run_line,
                    f"cannot lint a `run:` step with shell {shell!r}; expected one of "
                    f"{sorted(_PREAMBLES)} or a non-shell {sorted(_NOT_SHELL)}",
                )
            )
            continue
        first, column, exact = _locate(value, lines)
        scripts.append(RunScript(run_line, first, column, exact, dialect, value.value))
    return scripts, problems


def shellcheck_input(script: RunScript) -> str:
    """What shellcheck reads on stdin: the runner's preamble, then the masked body."""
    return f"{_PREAMBLES[script.shell]}\n{mask_expressions(script.body)}"


def shellcheck_command(script: RunScript) -> list[str]:
    """actionlint's own flags (see `_EXCLUDED`), reading the script from stdin."""
    return [
        "shellcheck",
        "--norc",
        "-f",
        "json",
        "-x",
        "--shell",
        script.shell,
        "-e",
        _EXCLUDED,
        "-",
    ]


def format_findings(script: RunScript, rel: Path, comments: list[dict]) -> list[str]:
    """One `file:line:start-end: SCnnnn (level): message` per shellcheck comment.

    The shape .vscode/tasks.json's problem matcher parses, same as the repo's other checks.
    """
    found = []
    for c in comments:
        if script.exact:
            # Input line 1 is the preamble, so input line N is body line N - 1.
            line = script.first_line + c["line"] - 2
            offset = script.column - 1
            cols = (c["column"] + offset, c["endColumn"] + offset)
        else:
            line, cols = script.run_line, (1, 2)
        found.append(
            f"{rel}:{line}:{cols[0]}-{cols[1]}: SC{c['code']} ({c['level']}): {c['message']}"
        )
    return found


def lint(script: RunScript, rel: Path) -> list[str]:
    result = subprocess.run(
        shellcheck_command(script), input=shellcheck_input(script), capture_output=True, text=True
    )
    # 0 is clean and 1 is findings; anything else is shellcheck itself failing, which is no pass.
    if result.returncode not in (0, 1):
        return [f"{rel}:{script.run_line}:1-2: shellcheck failed: {result.stderr.strip()}"]
    return format_findings(script, rel, json.loads(result.stdout or "[]"))


def main() -> int:
    root = workspace_root()
    actions = [p for p in action_yaml_files(root) if p.is_relative_to(root / ".github" / "actions")]
    problems: list[str] = []
    checked = 0
    for action in actions:
        rel = action.relative_to(root)
        scripts, unlintable = run_scripts(action.read_text(encoding="utf-8"))
        problems += [f"{rel}:{line}:1-2: {message}" for line, message in unlintable]
        for script in scripts:
            problems += lint(script, rel)
            checked += 1

    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} problem(s) in composite action scripts.", file=sys.stderr)
        return 1
    print(f"shellcheck is clean over {checked} run: step(s) in {len(actions)} composite action(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
