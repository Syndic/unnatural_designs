"""Holds the Semgrep job's per-rule timeout, and the input it was sized against.

`--timeout` is semgrep's per-rule, per-file budget. Over budget, semgrep does not drop the rule —
it drops **the whole file**, reports `CI scan completed successfully`, and exits 0. So a file
leaving the secret scan is indistinguishable from a file scanned clean, and is visible only to
someone reading a green job's log. That is #294: one `p/secrets` rule needs 5.980s on
`MODULE.bazel.lock` against a 5s default, so the lock file left every full scan for months.

Nothing else catches the flag being dropped. `bazel test //...` stays green, because the other two
suites that load this workflow assert only on their own jobs. The Semgrep job itself stays green on
a pull request, because `semgrep ci` diff-scans there (`Targets scanned: 1`) and never reaches the
lock file. Only push-to-main and the weekly schedule run the full scan, and those are the runs with
no reviewer attached — so the regression would land silently and sit there. The assertion is the
whole gate.

The second test is the same failure wearing a different hat. The budget was sized against a
measurement of one file at one size, and the rule's cost scales about linearly with input:
measured under the pinned image at 197 KB / 394 KB / 788 KB, the rule takes 2.956s / 6.714s /
13.894s. `MODULE.bazel.lock` is generated, so it grows on ordinary dependency additions with
nobody watching its size — and when it outgrows the budget the failure is the silent skip again,
not a red job. Pinning the size makes that expiry loud while the budget still holds.
"""

import unittest
from pathlib import Path

import yaml

# Not .resolve(): both files are cross-package data deps, so each lives in the runfiles tree beside
# this one rather than at the source path a resolved symlink would lead back to.
_ROOT = Path(__file__).parent.parent.parent
_WORKFLOW = _ROOT / ".github" / "workflows" / "security.yml"
_LOCK = _ROOT / "MODULE.bazel.lock"

# The rule needs 5.980s on the runner (#294, run 33916057420). Raising this is a real decision, not
# a formality: the budget is global, so a roomier value lets a future pathological rule burn that
# much longer per file before being skipped.
_MIN_TIMEOUT_SECONDS = 15

# Predicted runner cost at this size is ~11.8s against the 15s budget, so the gate fires with the
# budget still holding rather than after a scan has already been dropped. Do not raise it to quiet
# a red run: the fix is a fresh runner measurement, and then the timeout, or neither.
_MAX_LOCK_BYTES = 400_000


def semgrep_run_step() -> str:
    """The `semgrep ci` command line from the Semgrep job.

    Structural rather than a regex over the file: a `--timeout` anywhere in `security.yml` is not
    evidence about this step, and #268 is converting the workflow readers off text slicing rather
    than adding to it.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["semgrep"]["steps"]
    commands = [s["run"] for s in steps if "run" in s and s["run"].strip().startswith("semgrep ci")]
    if len(commands) != 1:
        raise AssertionError(f"expected exactly one `semgrep ci` step, found {len(commands)}")
    return " ".join(commands[0].split())


class SemgrepBudgetTest(unittest.TestCase):
    def test_the_scan_carries_a_raised_per_rule_budget(self):
        command = semgrep_run_step()
        self.assertIn(
            "--timeout",
            command,
            "without it semgrep's 5s default drops MODULE.bazel.lock from every full scan and "
            "still exits 0 — the job goes green having skipped the file (#294)",
        )
        value = float(command.split("--timeout", 1)[1].split()[0])
        self.assertGreaterEqual(
            value,
            _MIN_TIMEOUT_SECONDS,
            f"the rule needs 5.980s on the runner; below {_MIN_TIMEOUT_SECONDS}s the margin for "
            "runner variance and in-situ contention is gone and the silent skip returns",
        )

    def test_the_lock_file_has_not_outgrown_the_budget(self):
        size = _LOCK.stat().st_size
        self.assertLessEqual(
            size,
            _MAX_LOCK_BYTES,
            f"MODULE.bazel.lock is {size} bytes; the rule's cost scales about linearly with it, so "
            "the timeout above is no longer sized against reality. Re-measure on the runner and "
            "raise the timeout — raising this bound alone restores the silent skip (#294)",
        )
