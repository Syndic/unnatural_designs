"""Tests for renovate_manual_job.py.

`tick_manual_job` carries all the logic; the stdin/stdout and $GITHUB_OUTPUT wiring is exercised
end-to-end by renovate-run-after-automerge.yml.

DASHBOARD mirrors the real issue body's shape — a `rebase-branch=` checkbox per open PR, the
`rebase-all-open-prs` one, then Mend's `manual job` footer — so the "sibling checkboxes survive"
guarantee is tested against the layout that actually ships. Note the sibling rows carry a leading
space and the footer does not; both spellings are real.
"""

import unittest

from meta.scripts.renovate_manual_job import (
    ABSENT,
    ALREADY_TICKED,
    TICKED,
    tick_manual_job,
)

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
