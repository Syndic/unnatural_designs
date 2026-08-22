"""Holds the shared module-check action to the two couplings that fail silently.

`ci.yml` and `security.yml` both need the module completeness check — CI gates golangci-lint's
module matrix on it, security.yml gates govulncheck's and pip-audit's — and jobs cannot be
depended on across workflows, so both run it. `.github/actions/check-modules` exists so the two
callers share one definition of *how*: before it, each restated the Python pin and two action
SHAs, and a diverged copy would have run the same check under a different interpreter in each
workflow with nothing to say so.

That is the first coupling here: a caller that re-inlines the steps gets its own copy back, and
CI stays green while it drifts.

The second is the Renovate pattern. The pin moved out of `.github/workflows/` when the steps did,
and the customManager that claims `# renovate:` markers is scoped by file path — so the pattern
had to widen to `.github/actions/*/action.yml` in the same change. README's "Marker-driven"
records that this class of failure is silent and has already happened twice: the pin simply stops
moving and Renovate says nothing.
"""

import json
import re
import unittest
from pathlib import Path

# Not .resolve(): every file read here is a cross-package data dep, so each lives in the runfiles
# tree beside this one rather than at the source path a resolved symlink would lead back to.
_ROOT = Path(__file__).parent.parent.parent
# Repo-relative, because that is what Renovate matches its file patterns against — passing a
# bare filename here would miss every anchored pattern and quietly claim nothing.
_ACTION_PATH = ".github/actions/check-modules/action.yml"
_ACTION = _ROOT / _ACTION_PATH
_RENOVATE = _ROOT / "renovate.json"
_CALLERS = (
    _ROOT / ".github" / "workflows" / "ci.yml",
    _ROOT / ".github" / "workflows" / "security.yml",
)

# The path a workflow `uses:` to reach the action, and the script that action is the wrapper for.
_USES = "uses: ./.github/actions/check-modules"
_SCRIPT = "meta/scripts/check_modules.py"


def job_block(text: str, job: str) -> str:
    """One job's lines: its key through the line before the next job key."""
    start = re.search(rf"^  {re.escape(job)}:$", text, re.M)
    if start is None:
        raise AssertionError(f"no `{job}:` job")
    rest = text[start.end() :]
    end = re.search(r"^  [a-zA-Z_][\w-]*:$", rest, re.M)
    return rest[: end.start()] if end else rest


class DelegationTest(unittest.TestCase):
    """Both callers reach the check through the action rather than through steps of their own."""

    def test_every_caller_uses_the_action(self):
        for caller in _CALLERS:
            with self.subTest(workflow=caller.name):
                block = job_block(caller.read_text(encoding="utf-8"), "modules-check")
                self.assertIn(
                    _USES,
                    block,
                    "this job runs the module check without the shared action, so its setup "
                    "steps are a second copy that nothing holds to the first",
                )

    def test_no_caller_reinlines_the_script(self):
        """The tell that a copy came back: the workflow invoking the script directly again."""
        for caller in _CALLERS:
            with self.subTest(workflow=caller.name):
                block = job_block(caller.read_text(encoding="utf-8"), "modules-check")
                self.assertNotIn(_SCRIPT, block, "call it through the action, not beside it")

    def test_the_action_runs_the_script(self):
        """Non-vacuity guard: the assertions above mean nothing if the action does not run it."""
        self.assertIn(_SCRIPT, _ACTION.read_text(encoding="utf-8"))


class RenovateCoverageTest(unittest.TestCase):
    """The action's pin is only tracked while a customManager's file pattern reaches it."""

    def setUp(self):
        self.action = _ACTION.read_text(encoding="utf-8")
        config = json.loads(_RENOVATE.read_text(encoding="utf-8"))
        self.managers = [m for m in config["customManagers"] if m["customType"] == "regex"]

    def claims(self, path: str, text: str) -> list[tuple[str | None, str]]:
        """Every (depName, currentValue) a manager whose pattern reaches `path` finds in `text`.

        depName is None for the managers that name the dep in `depNameTemplate` instead of a
        capture group, so callers filter on it rather than assuming it is there.
        """
        found = []
        for manager in self.managers:
            patterns = [re.compile(p.strip("/")) for p in manager["managerFilePatterns"]]
            if not any(p.search(path) for p in patterns):
                continue
            for match in manager["matchStrings"]:
                # Renovate's regexes are JS-flavoured; only the named-group spelling differs.
                for hit in re.finditer(match.replace("(?<", "(?P<"), text):
                    groups = hit.groupdict()
                    found.append((groups.get("depName"), groups["currentValue"]))
        return found

    def test_the_action_carries_a_python_pin(self):
        """Non-vacuity guard: a pin that moved back out would pass the coverage test trivially."""
        self.assertRegex(
            self.action,
            r'python-version: "\d+\.\d+"',
            "the action no longer pins Python; the coverage assertion below has nothing to hold",
        )

    def test_renovate_claims_the_actions_python_pin(self):
        claimed = self.claims(_ACTION_PATH, self.action)
        self.assertIn(
            "python",
            [dep for dep, _ in claimed],
            "no customManager pattern reaches `.github/actions/*/action.yml`, so this pin is "
            "frozen — Renovate reports nothing when a marker stops being claimed",
        )

    def test_the_pin_matches_the_workflows_that_still_carry_one(self):
        """Divergent copies are two dependencies to Renovate, and they drift apart separately."""
        versions = {
            value for dep, value in self.claims(_ACTION_PATH, self.action) if dep == "python"
        }
        self.assertTrue(versions, "the action's own pin was not read; the comparison is vacuous")
        for caller in _CALLERS:
            text = caller.read_text(encoding="utf-8")
            versions |= {
                value
                for dep, value in self.claims(f".github/workflows/{caller.name}", text)
                if dep == "python"
            }
        self.assertEqual(
            len(versions),
            1,
            f"the Python pin is spelled {sorted(versions)} across the action and its callers; "
            "keep duplicate pins byte-identical or Renovate tracks them as separate updates",
        )


if __name__ == "__main__":
    unittest.main()
