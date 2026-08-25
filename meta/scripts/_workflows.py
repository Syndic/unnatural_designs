"""Reads GitHub Actions YAML structurally: per-language module matrices, and step inputs.

Split out of `_workspace.py` so the YAML dependency lands on the scripts that need it:
`check_modules.py` reads matrices and `check_python_version.py` reads step inputs, while
`check_go_work.py`, `check_adr_numbers.py`, `check_secrets_dir.py` and `check_no_cgo.py` do
not, and each of those is its own CI job.

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

What is still reported is what a parse cannot settle: an axis present under the key whose value
is not a list of paths. What is not reported is a wholly computed `matrix:`, where the key's
presence is itself unknown.
"""

from pathlib import Path
from typing import NamedTuple

import yaml

# (job_name, key_line, {entry_path: line_number}) — the shape callers destructure.
MatrixBlock = tuple[str, int, dict[Path, int]]

# Coverage is the base axis plus `include:`; `exclude:` is ignored in both directions. That is a
# policy call, not a property of YAML — a module excluded for cause has been handled, which is the
# question this check asks. GitHub only ever lets `exclude:` shrink the cross product, so its
# values are recognised as a legitimate use of the key but never collected: naming a module there
# and nowhere else leaves it genuinely unscanned, and the caller reports it missing.
# See //meta/scripts:test__workflows for both halves.
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
            # `matrix: ${{ fromJSON(...) }}`. Deliberately silent, and the line the whole
            # reporting policy sits on: report where the key is *present* and unreadable, stay
            # quiet where we cannot tell whether the key is there at all. A wholly computed
            # matrix is the second case — it is usually an unrelated axis (an OS list, a shard
            # count), and reporting it would fail CI on workflows that never mention Go. That is
            # the false-positive class this module exists to have removed, and there would be no
            # escape hatch for an author who legitimately wants a dynamic matrix. The cost is
            # that a per-module matrix converted to `fromJSON` stops being verified without
            # saying so; tracked separately rather than paid for by everyone else's workflows.
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

    Not a spelling the parser failed to recognise — that whole category is gone now that the YAML
    is parsed rather than matched. What reaches here is an axis that is *present* under this key
    and is not a list of paths, most likely a `fromJSON` expression. It needs a person, and
    reporting it is what keeps a guard that cannot read something from reading as a guard that
    saw nothing wrong.

    A wholly computed `matrix:` is deliberately not reported — see the note at that branch in
    `_scan`. The key may not be involved at all, and guessing that it is fails CI on workflows
    with no relationship to this language.
    """
    return _scan(workflow_file, matrix_key)[1]


class Step(NamedTuple):
    """One step, with the enclosing job's matrix axes attached.

    `matrix` travels with the step because a `${{ matrix.x }}` input is only checkable against
    the axis it names, and that axis lives two levels up. Empty for a composite action, which
    has no `strategy:` — and where a `matrix.` reference would not resolve at run time either.
    """

    uses: str | None
    line: int
    inputs: dict[str, tuple[str | None, int]]
    matrix: dict[str, list[str]]


def _matrix_axes(job: yaml.Node | None) -> dict[str, list[str]]:
    """`{axis: [values]}` a job's `strategy.matrix` actually runs, in GitHub's own order.

    Base list, minus `exclude:`, plus `include:` — GitHub processes `include:` after `exclude:`
    precisely so an excluded combination can be added back, and a set computed in any other order
    would not be the one the job runs.

    Note this reads `exclude:` where `_scan` deliberately ignores it, because the two callers ask
    opposite questions. For matrix *completeness*, ignoring `exclude:` errs safe: a module named
    only there is genuinely unscanned, and the caller reports it missing. For *containment*,
    shrinking the run set is the whole risk, so ignoring `exclude:` would err the wrong way — an
    axis could list the pin while excluding every combination that uses it.

    Only a single-key `exclude:` entry subtracts. A multi-key entry removes some combinations and
    leaves others, so the value survives on the axis and must not be subtracted from it.

    An axis whose value is not a list of scalars is *unreadable*, and stays that way: `include:`
    may extend an axis that was readable or absent, never conjure one over a value this could not
    parse. Otherwise a computed axis plus one `include:` entry would read as a known one-element
    list, which is exactly the "unreadable is indistinguishable from correct" case the callers
    refuse.
    """
    strategy = _lookup(job, "strategy")
    if strategy is None:
        return {}
    matrix = _lookup(strategy[1], "matrix")
    if matrix is None or not isinstance(matrix[1], yaml.MappingNode):
        return {}

    axes: dict[str, list[str]] = {}
    unreadable: set[str] = set()
    for key_node, value_node in _pairs(matrix[1]):
        name = key_node.value
        if name in (_INCLUDE, _EXCLUDE):
            continue
        if not isinstance(value_node, yaml.SequenceNode):
            unreadable.add(name)
            continue
        values = [i.value for i in value_node.value if isinstance(i, yaml.ScalarNode)]
        if len(values) == len(value_node.value):
            axes[name] = values
        else:
            unreadable.add(name)

    for name, value in _single_key_entries(_lookup(matrix[1], _EXCLUDE)):
        if name in axes:
            axes[name] = [v for v in axes[name] if v != value]

    for name, value in _single_key_entries(_lookup(matrix[1], _INCLUDE), single_only=False):
        if name not in unreadable:
            axes.setdefault(name, []).append(value)
    return axes


def _single_key_entries(
    block: tuple[yaml.ScalarNode, yaml.Node] | None, single_only: bool = True
) -> list[tuple[str, str]]:
    """`(key, value)` scalar pairs from an `include:`/`exclude:` list.

    `single_only` keeps `exclude:` honest: an entry naming two axes removes only the combinations
    matching both, so neither value leaves its axis. `include:` has no such constraint — every
    scalar it names is a value that combination actually runs with.
    """
    found: list[tuple[str, str]] = []
    if block is None or not isinstance(block[1], yaml.SequenceNode):
        return found
    for item in block[1].value:
        pairs = _pairs(item)
        if single_only and len(pairs) != 1:
            continue
        found.extend((k.value, v.value) for k, v in pairs if isinstance(v, yaml.ScalarNode))
    return found


def _step_nodes(root: yaml.Node | None) -> list[tuple[yaml.Node, dict[str, list[str]]]]:
    """Step nodes, paired with their job's matrix axes.

    Workflow `jobs.*.steps` and composite-action `runs.steps` in one walk, because the two file
    kinds are the same question asked twice: a step is a step whether a job or a composite action
    holds it, and a guard that reads only one of them has a blind spot exactly where nobody looks.
    """
    found: list[tuple[yaml.Node, dict[str, list[str]]]] = []
    containers: list[tuple[yaml.Node, dict[str, list[str]]]] = []

    jobs = _lookup(root, "jobs")
    if jobs is not None:
        containers.extend((job, _matrix_axes(job)) for _, job in _pairs(jobs[1]))

    runs = _lookup(root, "runs")
    if runs is not None:
        containers.append((runs[1], {}))

    for container, axes in containers:
        block = _lookup(container, "steps")
        if block is not None and isinstance(block[1], yaml.SequenceNode):
            found.extend((node, axes) for node in block[1].value)
    return found


def action_steps(yaml_file: Path) -> tuple[list[Step], dict[int, str]]:
    """Every step in a workflow or composite action, as (uses, line, {input: (value, line)}).

    Structural for the same reason the matrix reader is: a step's `with:` values are only
    distinguishable from identically-named keys elsewhere — a `strategy.matrix` axis, a job
    `env:` entry, a line inside a `run: |` heredoc — by where they sit. Flow style
    (`- {uses: x, with: {k: v}}`) composes to the same nodes as block style, so it needs no
    second spelling to recognise.

    `uses` is None for a `run:` step. A non-scalar input value (a `${{ }}` block, a nested
    mapping) is reported as None rather than dropped, so a caller can tell "absent" from
    "present but not a plain string".
    """
    problems: dict[int, str] = {}
    try:
        root = yaml.compose(yaml_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problems[mark.line + 1 if mark else 1] = f"could not be parsed as YAML ({exc.args[0]})"
        return [], problems

    collected: list[Step] = []
    for node, axes in _step_nodes(root):
        if not isinstance(node, yaml.MappingNode):
            continue

        uses_pair = _lookup(node, "uses")
        uses = None
        if uses_pair is not None and isinstance(uses_pair[1], yaml.ScalarNode):
            uses = uses_pair[1].value

        inputs: dict[str, tuple[str | None, int]] = {}
        with_pair = _lookup(node, "with")
        if with_pair is not None:
            for key_node, value_node in _pairs(with_pair[1]):
                value = value_node.value if isinstance(value_node, yaml.ScalarNode) else None
                inputs[key_node.value] = (value, _line(key_node))

        collected.append(Step(uses, _line(node), inputs, axes))
    return collected, problems
