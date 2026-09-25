"""Every caller of classify_changed_paths.py asks for sets that exist, and reads only what it asked.

Two hops, each silent when it breaks, held for every workflow and composite action at once — found
by glob, so a new caller is covered with no edit here:

  - **The `--emit` names.** Read by the parser the step runs, then resolved by `select()`, which
    refuses a name it does not know. That fails the step, but on a PR, after the rename is written.
  - **The reads of the step's outputs.** A `steps.<id>.outputs.<name>` naming a set the step did
    not emit is an empty string, which reads as `false`: whatever it gates never runs, and nothing
    goes red.

What stays with a caller's own workflow test is whatever is particular to it — devcontainer.yml's
job-level `outputs:` hop in `//meta/scripts:test_devcontainer_required_checks`, and the
self-test's gate reading the one set it needs in `:test_commit_file_via_app_selftest`.
"""

import json
import re
import unittest
from pathlib import Path
from typing import NamedTuple

from meta.scripts._workflows import action_yaml_files, step_containers
from meta.scripts.classify_changed_paths import emitted_sets, invocations, select

# Not .resolve(): the workflows and actions are cross-package data deps, so they live in the
# runfiles tree beside this file rather than at the source path a resolved symlink leads back to.
_ROOT = Path(__file__).parent.parent.parent


class Caller(NamedTuple):
    """One step that runs the classifier, with what it asks for and what its job reads back."""

    where: str
    argvs: list[list[str]]
    reads: set[str]


def callers() -> list[Caller]:
    found = []
    for path in action_yaml_files(_ROOT):
        for label, body in step_containers(path):
            for step in body.get("steps") or []:
                argvs = invocations(step.get("run") or "")
                if not argvs:
                    continue
                step_id = step.get("id")
                reads = set()
                if step_id is not None:
                    # The whole job as text: an output is read from any `if:`, `env:`, `with:` or
                    # `run:` in it, and from the job's own `outputs:`.
                    pattern = re.escape(f"steps.{step_id}.outputs.") + r"([\w-]+)"
                    reads = set(re.findall(pattern, json.dumps(body)))
                where = f"{path.relative_to(_ROOT)}:{label}:{step_id or step.get('name')}"
                found.append(Caller(where, argvs, reads))
    return found


class ClassifierCallersTest(unittest.TestCase):
    def setUp(self):
        self.callers = callers()

    def test_callers_were_found(self):
        """Without the data deps, or after a rename, every assertion below passes vacuously."""
        self.assertTrue(self.callers, "no step in the runfiles tree runs the classifier")

    def test_some_caller_reads_an_output(self):
        """Non-vacuity for the read check: a pattern that matched nothing would pass it."""
        self.assertTrue(any(caller.reads for caller in self.callers))

    def test_every_invocation_parses(self):
        for caller in self.callers:
            for argv in caller.argvs:
                with self.subTest(caller=caller.where, argv=argv):
                    try:
                        emitted_sets(argv)
                    except SystemExit:
                        self.fail(f"{caller.where} passes arguments the classifier refuses")

    def test_every_emitted_set_exists(self):
        for caller in self.callers:
            for argv in caller.argvs:
                with self.subTest(caller=caller.where, argv=argv):
                    try:
                        select(emitted_sets(argv))
                    except SystemExit as exc:
                        self.fail(f"{caller.where}: {exc}")

    def test_every_read_names_a_set_the_step_emitted(self):
        for caller in self.callers:
            with self.subTest(caller=caller.where):
                emitted = {name for argv in caller.argvs for name in emitted_sets(argv)}
                self.assertEqual(
                    sorted(caller.reads - emitted),
                    [],
                    f"{caller.where}'s job reads outputs its classifier step never emits; each "
                    "reads as `false` on every run, so whatever it gates never runs",
                )


if __name__ == "__main__":
    unittest.main()
