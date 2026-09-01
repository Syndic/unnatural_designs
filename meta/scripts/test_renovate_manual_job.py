"""Tests for renovate_manual_job.py.

`tick_manual_job` carries all the logic; the stdin/stdout and $GITHUB_OUTPUT wiring is exercised
end-to-end by renovate-run-after-automerge.yml.

DASHBOARD mirrors the real issue body's shape — a `rebase-branch=` checkbox per open PR, the
`rebase-all-open-prs` one, then Mend's `manual job` footer — so the "sibling checkboxes survive"
guarantee is tested against the layout that actually ships. Note the sibling rows carry a leading
space and the footer does not; both spellings are real.
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml
from meta.scripts.renovate_manual_job import (
    ABSENT,
    ALREADY_TICKED,
    STATE_ABSENT,
    STATE_CLEAR,
    STATE_TICKED,
    TICKED,
    main,
    manual_job_state,
    tick_manual_job,
)

# Not .resolve(): the workflow is a cross-package data dep, so it lives in the runfiles tree
# beside this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = (
    Path(__file__).parent.parent.parent / ".github/workflows/renovate-run-after-automerge.yml"
)
_VERIFY_STEP = "Verify Renovate acted on the request"


def verify_step_script() -> str:
    """The shell body of the poll step whose `case` arms this file pins."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["request-run"]["steps"]:
        if step.get("name") == _VERIFY_STEP:
            return step["run"]
    raise AssertionError(f"no step named {_VERIFY_STEP!r} in {_WORKFLOW}")


MANUAL_JOB_LINE = (
    "- [ ] <!-- manual job -->Check this box to trigger a request for Renovate "
    "to run again on this repository"
)

DASHBOARD = (
    "This issue lists Renovate updates and detected dependencies.\n"
    "\n"
    "## Open\n"
    "\n"
    "The following updates have all been created. To force a retry/rebase of any, click on a\n"
    "checkbox below.\n"
    "\n"
    " - [ ] <!-- rebase-branch=renovate/all-minor-patch -->[chore(deps): update all non-major"
    " dependencies](../pull/216)\n"
    " - [ ] <!-- rebase-all-open-prs -->**Click on this checkbox to rebase all open PRs at"
    " once**\n"
    "\n"
    "---\n"
    f"{MANUAL_JOB_LINE}\n"
)


class TestStatusContract(unittest.TestCase):
    def test_status_values_are_the_workflow_contract(self):
        # renovate-run-after-automerge.yml gates steps on these literals
        # (`steps.tick.outputs.status == 'absent'`). Comparing only against the imported
        # constants elsewhere would let a rename pass every test while the workflow's
        # missing-checkbox guard silently stopped firing.
        self.assertEqual((TICKED, ALREADY_TICKED, ABSENT), ("ticked", "already-ticked", "absent"))

    def test_state_values_are_the_probe_contract(self):
        # The scheduled probe's polling loop branches on these literals coming out of `--check`.
        self.assertEqual((STATE_CLEAR, STATE_TICKED, STATE_ABSENT), ("clear", "ticked", "absent"))


class TestManualJobState(unittest.TestCase):
    """`manual_job_state` backs the scheduled probe's wait-for-Renovate-to-clear-it loop."""

    def test_clear_when_unticked(self):
        self.assertEqual(manual_job_state(DASHBOARD), STATE_CLEAR)

    def test_ticked_after_our_own_edit(self):
        # The exact transition the probe watches: we tick, then poll until Renovate clears it.
        ticked, _ = tick_manual_job(DASHBOARD)
        self.assertEqual(manual_job_state(ticked), STATE_TICKED)

    def test_ticked_uppercase(self):
        self.assertEqual(
            manual_job_state(DASHBOARD.replace("- [ ] <!-- manual job", "- [X] <!-- manual job")),
            STATE_TICKED,
        )

    def test_absent_without_the_marker(self):
        self.assertEqual(manual_job_state(DASHBOARD.replace(MANUAL_JOB_LINE, "")), STATE_ABSENT)

    def test_absent_on_empty_body(self):
        self.assertEqual(manual_job_state(""), STATE_ABSENT)

    def test_agrees_with_the_transform(self):
        # The drift guard. A probe that recognised a shape the transform no longer writes would
        # report `clear` forever and pass while the mechanism was broken, so pin the two together.
        cases = {
            DASHBOARD: (STATE_CLEAR, TICKED),
            DASHBOARD.replace("- [ ] <!-- manual job", "- [x] <!-- manual job"): (
                STATE_TICKED,
                ALREADY_TICKED,
            ),
            DASHBOARD.replace(MANUAL_JOB_LINE, ""): (STATE_ABSENT, ABSENT),
        }
        for body, (state, status) in cases.items():
            with self.subTest(state=state):
                self.assertEqual(manual_job_state(body), state)
                self.assertEqual(tick_manual_job(body)[1], status)


class TestCheckMode(unittest.TestCase):
    """`--check` is the CLI renovate-run-after-automerge.yml's polling loop actually invokes.

    The state tests above call `manual_job_state` directly, which leaves the wiring between it and
    the workflow untested: a stray `print` in the `--check` arm, or routing the answer through
    `$GITHUB_OUTPUT` instead of stdout, would keep every other test green while the loop read a
    state it has no arm for and ran out its ten-minute clock blaming Mend. So assert the exact
    bytes on stdout, not merely that the right word appears somewhere in them.
    """

    def _check(self, body):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(body)), contextlib.redirect_stdout(out):
            code = main(["--check"])
        return code, out.getvalue()

    def test_clear(self):
        self.assertEqual(self._check(DASHBOARD), (0, "clear\n"))

    def test_ticked(self):
        ticked, _ = tick_manual_job(DASHBOARD)
        self.assertEqual(self._check(ticked), (0, "ticked\n"))

    def test_absent(self):
        self.assertEqual(self._check(DASHBOARD.replace(MANUAL_JOB_LINE, "")), (0, "absent\n"))

    def test_empty_body(self):
        self.assertEqual(self._check(""), (0, "absent\n"))

    def test_emits_only_the_state(self):
        # The workflow captures stdout wholesale; anything else on it becomes the state string.
        _, out = self._check(DASHBOARD)
        self.assertEqual(out.splitlines(), ["clear"])

    def test_check_does_not_write_the_body(self):
        # `--check` returns before the transform, so a caller cannot mistake it for a tick.
        _, out = self._check(DASHBOARD)
        self.assertNotIn("<!-- manual job -->", out)

    def test_every_state_has_a_workflow_arm(self):
        # Named for the workflow, so it reads the workflow. The CLI's emitted set and the poll
        # loop's `case` arms are two halves of one contract; asserting only the Python half would
        # leave a deleted arm green here and a ten-minute timeout in production.
        emitted = {
            self._check(body)[1].strip()
            for body in (
                DASHBOARD,
                tick_manual_job(DASHBOARD)[0],
                DASHBOARD.replace(MANUAL_JOB_LINE, ""),
                "",
            )
        }
        self.assertEqual(emitted, {STATE_CLEAR, STATE_TICKED, STATE_ABSENT})

        script = verify_step_script()
        for state in sorted(emitted):
            with self.subTest(arm=state):
                self.assertIn(f"{state})", script)
        # The catch-all is what turns an unrecognised state into a loud failure naming this repo,
        # rather than a silent fall-through to the timeout that blames Mend.
        self.assertIn("*)", script)


class TestTickManualJob(unittest.TestCase):
    def test_ticks_the_footer_checkbox(self):
        body, status = tick_manual_job(DASHBOARD)
        self.assertEqual(status, TICKED)
        self.assertIn("- [x] <!-- manual job -->", body)

    def test_change_is_exactly_one_character(self):
        # The caller PATCHes the whole issue body, so anything else this rewrites is collateral
        # damage on text Renovate owns.
        body, _ = tick_manual_job(DASHBOARD)
        self.assertEqual(len(body), len(DASHBOARD))
        differing = [i for i, (a, b) in enumerate(zip(DASHBOARD, body, strict=True)) if a != b]
        self.assertEqual(len(differing), 1)

    def test_sibling_checkboxes_are_untouched(self):
        # Ticking a `rebase-branch=` box would force an unwanted rebase of that one PR, and
        # `rebase-all-open-prs` would rebase every one of them.
        body, _ = tick_manual_job(DASHBOARD)
        self.assertIn(" - [ ] <!-- rebase-branch=renovate/all-minor-patch -->", body)
        self.assertIn(" - [ ] <!-- rebase-all-open-prs -->", body)

    def test_already_ticked_is_a_no_op(self):
        # A run is already pending; re-PATCHing would be a wasted write and a second webhook.
        already = DASHBOARD.replace("- [ ] <!-- manual job", "- [x] <!-- manual job")
        body, status = tick_manual_job(already)
        self.assertEqual(status, ALREADY_TICKED)
        self.assertEqual(body, already)

    def test_already_ticked_uppercase(self):
        already = DASHBOARD.replace("- [ ] <!-- manual job", "- [X] <!-- manual job")
        body, status = tick_manual_job(already)
        self.assertEqual(status, ALREADY_TICKED)
        self.assertEqual(body, already)

    def test_absent_marker(self):
        # Mend owns this checkbox and has removed it before (renovatebot/renovate#20386), so
        # absence is a state the caller has to handle rather than a crash.
        without = DASHBOARD.replace(MANUAL_JOB_LINE, "")
        body, status = tick_manual_job(without)
        self.assertEqual(status, ABSENT)
        self.assertEqual(body, without)

    def test_empty_body(self):
        self.assertEqual(tick_manual_job(""), ("", ABSENT))

    def test_leading_whitespace_tolerated(self):
        body, status = tick_manual_job("   - [ ] <!-- manual job -->Check this box\n")
        self.assertEqual(status, TICKED)
        self.assertEqual(body, "   - [x] <!-- manual job -->Check this box\n")

    def test_asterisk_bullet_tolerated(self):
        body, status = tick_manual_job("* [ ] <!-- manual job -->Check this box\n")
        self.assertEqual(status, TICKED)
        self.assertEqual(body, "* [x] <!-- manual job -->Check this box\n")

    def test_comment_spacing_variants(self):
        for marker in ("<!--manual job-->", "<!--   manual job   -->", "<!--\tmanual job\t-->"):
            with self.subTest(marker=marker):
                _, status = tick_manual_job(f"- [ ] {marker}Check this box\n")
                self.assertEqual(status, TICKED)

    def test_other_markers_do_not_match(self):
        # Anchored on the `manual job` marker specifically, not on "a checkbox near the end".
        _, status = tick_manual_job(" - [ ] <!-- rebase-all-open-prs -->Rebase everything\n")
        self.assertEqual(status, ABSENT)

    def test_marker_without_a_checkbox_does_not_match(self):
        # Prose mentioning the marker is not a checkbox to tick.
        _, status = tick_manual_job("See the <!-- manual job --> checkbox below.\n")
        self.assertEqual(status, ABSENT)

    def test_checkbox_and_marker_on_different_lines_do_not_match(self):
        _, status = tick_manual_job("- [ ] Something else\n<!-- manual job -->\n")
        self.assertEqual(status, ABSENT)

    def test_first_line_of_body_matches(self):
        # `^` is MULTILINE-anchored, so a body starting with the checkbox still matches.
        _, status = tick_manual_job("- [ ] <!-- manual job -->Check this box\n")
        self.assertEqual(status, TICKED)


if __name__ == "__main__":
    unittest.main()
