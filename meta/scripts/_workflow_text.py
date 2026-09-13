"""A workflow job as text, and its `run:` body as a script the tests can actually run.

The structural reader next door (`_workflows.py`) is the right tool for a matrix's contents and
the wrong one for a fan-in. What a fan-in guard has to settle is whether the job's shell *rejects*
a result, and nothing short of running that shell under bash answers it: asserting on the spelling
would pass only the idiom it was written against, and this repo has two — `if` in ci.yml and
security.yml, `case` in devcontainer.yml — that behave identically.

Consumers are //meta/scripts:test_ci_fan_ins and //meta/scripts:test_codeql_toolchain.
"""

import re
import subprocess
import textwrap

# Trailing comments introduce the *next* job rather than closing this one, and these workflows
# write comments quoting the very lines the guards look for — so a block that keeps them can
# satisfy an assertion out of prose written about a different job.
_TRAILING_COMMENTS_RE = re.compile(r"(?:^[ \t]*(?:#.*)?\n)+\Z", re.M)

# The `run: |` body of a step, dedented.
_RUN_SCRIPT_RE = re.compile(r"^ +run: \|\n((?:^ {10}.*\n|^\n)+)", re.M)


def job_block(text: str, job: str, *, where: str = "the workflow") -> str:
    """One job's lines: its key through its last line of YAML, comments for the next job dropped."""
    start = re.search(rf"^  {re.escape(job)}:$", text, re.M)
    if start is None:
        raise AssertionError(f"no `{job}:` job in {where}")
    rest = text[start.end() :]
    end = re.search(r"^  [a-zA-Z_][\w-]*:$", rest, re.M)
    return _TRAILING_COMMENTS_RE.sub("", rest[: end.start()] if end else rest)


def run_fan_in(block: str, needs_job: str, result: str) -> subprocess.CompletedProcess:
    """Run a fan-in job's shell with `result` standing in for the matrix job's outcome."""
    script = _RUN_SCRIPT_RE.search(block)
    if script is None:
        raise AssertionError("no `run: |` script in the fan-in job")
    body = textwrap.dedent(script.group(1))
    expression = f"${{{{ needs.{needs_job}.result }}}}"
    # A substitution that found nothing leaves `${{ ... }}` for bash, which errors on it and takes
    # the branch that exits 0 — so a wrong `needs_job` would read as a fan-in that accepts
    # anything. Nothing downstream can tell that apart from a real pass.
    if expression not in body:
        raise AssertionError(f"the fan-in's script does not read `{expression}`")
    return subprocess.run(
        ["bash", "-c", body.replace(expression, result)], capture_output=True, text=True
    )
