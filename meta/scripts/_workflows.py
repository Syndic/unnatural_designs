"""Reads per-language module matrices out of GitHub Actions workflows.

Split out of `_workspace.py` so the YAML dependency lands on the one script that needs it:
`check_modules.py` reads matrices, while `check_go_work.py`, `check_adr_numbers.py`,
`check_secrets_dir.py` and `check_no_cgo.py` do not, and each of those is its own CI job.

This is a real parse, not a scan. The line-oriented predecessor recognised a matrix only when
its key stood alone on a line, which meant flow style (`go_module: [a]`, `- { go_module: a }`)
produced no block and no complaint — the completeness guard exited 0 having checked nothing,
the exact failure it exists to prevent. Widening the text patterns to cover more spellings was
tried and produced its own class of bug: a regex with no notion of matrix context flagged
`workflow_call` inputs, step `with:` values, job `env:` entries and lines inside `run: |`
blocks, all of which are ordinary YAML that has nothing to do with a matrix.

Structure is the thing that distinguishes those cases, so the parser supplies it. Flow versus
block style stops being a question the code asks — both compose to the same node — and comments,
quoting and nesting depth stop mattering for the same reason.

`yaml.compose` rather than `yaml.safe_load`: it returns the node graph with source positions, so
diagnostics keep the `file:line:` anchors `.vscode/tasks.json`'s problem matcher consumes. It
also sidesteps YAML 1.1's boolean coercion of `on:` — node values are the literal key strings.
"""

from pathlib import Path

import yaml

# (job_name, key_line, {entry_path: line_number}) — the shape callers destructure.
MatrixBlock = tuple[str, int, dict[Path, int]]

# `include:` adds combinations, so its entries are part of the matrix. `exclude:` removes them:
# an excluded combination still names a module that must appear in the base axis, so its values
# are recognised (they are a legitimate use of the key) but never collected as entries.
_INCLUDE = "include"
_EXCLUDE = "exclude"


def _line(node: yaml.Node) -> int:
    """1-based source line of a node."""
    return node.start_mark.line + 1


def _pairs(node: yaml.Node | None) -> list[tuple[yaml.ScalarNode, yaml.Node]]:
    """A mapping's (key, value) node pairs; empty for anything that is not a mapping."""
    if not isinstance(node, yaml.MappingNode):
        return []
    return [(k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode)]


def _lookup(node: yaml.Node | None, key: str) -> tuple[yaml.ScalarNode, yaml.Node] | None:
    """The (key, value) nodes for `key`, or None. Last wins, as YAML mappings do."""
    found = None
    for key_node, value_node in _pairs(node):
        if key_node.value == key:
            found = (key_node, value_node)
    return found


def _scan(workflow_file: Path, matrix_key: str) -> tuple[list[MatrixBlock], dict[int, str]]:
    """Parse one workflow. Returns its matrix blocks and any un-checkable uses of the key.

    A "problem" is a place the key is used in a position this check cannot evaluate — a computed
    matrix, or a value that is not a list of paths. Those are reported rather than skipped,
    because a matrix nobody can read is indistinguishable from a workflow with no matrix, and
    the second reading is the one that passes.
    """
    blocks: list[MatrixBlock] = []
    problems: dict[int, str] = {}

    try:
        root = yaml.compose(workflow_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problems[mark.line + 1 if mark else 1] = f"could not be parsed as YAML ({exc.args[0]})"
        return blocks, problems

    jobs = _lookup(root, "jobs")
    if jobs is None:
        return blocks, problems

    for job_key, job in _pairs(jobs[1]):
        strategy = _lookup(job, "strategy")
        if strategy is None:
            continue
        matrix = _lookup(strategy[1], "matrix")
        if matrix is None:
            continue

        job_name = job_key.value
        if not isinstance(matrix[1], yaml.MappingNode):
            # `matrix: ${{ fromJSON(...) }}` — a real feature, and one no static check can
            # resolve. Naming it is the honest outcome; skipping it silently is not.
            problems[_line(matrix[1])] = (
                f"[{job_name}] `matrix` is computed, so its `{matrix_key}` axis cannot be checked"
            )
            continue

        entries: dict[Path, int] = {}
        key_line = -1

        def note(line: int) -> None:
            nonlocal key_line
            if key_line == -1:
                key_line = line

        axis = _lookup(matrix[1], matrix_key)
        if axis is not None:
            note(_line(axis[0]))
            if isinstance(axis[1], yaml.SequenceNode):
                for item in axis[1].value:
                    if isinstance(item, yaml.ScalarNode):
                        entries[Path(item.value)] = _line(item)
                    else:
                        problems[_line(item)] = f"[{job_name}] `{matrix_key}` entry is not a path"
            else:
                problems[_line(axis[1])] = f"[{job_name}] `{matrix_key}` is not a list of paths"

        for section in (_INCLUDE, _EXCLUDE):
            block = _lookup(matrix[1], section)
            if block is None or not isinstance(block[1], yaml.SequenceNode):
                continue
            for item in block[1].value:
                hit = _lookup(item, matrix_key)
                if hit is None:
                    continue
                note(_line(hit[0]))
                if not isinstance(hit[1], yaml.ScalarNode):
                    problems[_line(hit[1])] = (
                        f"[{job_name}] `{matrix_key}` in `{section}:` is not a path"
                    )
                elif section == _INCLUDE:
                    entries[Path(hit[1].value)] = _line(hit[1])

        if key_line != -1:
            blocks.append((job_name, key_line, entries))

    return blocks, problems


def workflow_matrix_lists(workflow_file: Path, matrix_key: str) -> list[MatrixBlock]:
    """Every `matrix.<matrix_key>` axis in one workflow, as (job, key_line, {path: line}).

    One block per `strategy.matrix` that names the key, with `include:` entries unioned in the
    way GitHub unions them — emitting those separately would report each form's entries as
    missing from the other. `exclude:` entries are deliberately absent: excluding a combination
    does not remove the module from the axis it was drawn from.

    ``key_line`` is where the key was first seen, and is the anchor for diagnostics that have no
    more specific line to point at (a module missing from the list has no offending token).
    """
    return _scan(workflow_file, matrix_key)[0]


def unrecognised_matrix_keys(workflow_file: Path, matrix_key: str) -> dict[int, str]:
    """Uses of ``matrix_key`` this check cannot evaluate, as {line: what was found}.

    Two cases reach here, and neither is a spelling the parser failed to recognise — that whole
    category is gone now that the YAML is parsed rather than matched. What remains is genuinely
    un-checkable: a matrix computed at run time (`fromJSON`), and an axis whose value is not a
    list of paths. Both need a person; reporting them is what keeps a guard that cannot see
    something from reading as a guard that saw nothing wrong.
    """
    return _scan(workflow_file, matrix_key)[1]
