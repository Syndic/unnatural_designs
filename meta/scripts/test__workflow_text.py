"""Covers `_workflow_text.py` directly, the way test__workspace covers its helper.

The fan-in guards that consume it are assertions about real workflows, so a slicing bug there
reads as a claim about the workflow rather than about the slicer. These are the cases that
distinguish the two: a block that runs to the end of the file, a block whose trailing comments
belong to the next job, and the two shapes that have no answer at all.
"""

import textwrap
import unittest

from meta.scripts._workflow_text import job_block, run_fan_in

_WORKFLOW = textwrap.dedent("""\
    jobs:
      first:
        name: First
        needs: [zeroth]
        steps:
          - run: |
              echo first

      # This comment introduces `second`, and sits inside `first`'s lines.
      second:
        name: Second
        if: always()
        steps:
          - name: Verify
            run: |
              if [ "${{ needs.first.result }}" != "success" ]; then
                exit 1
              fi
              echo ok
    """)


class JobBlockTest(unittest.TestCase):
    def test_block_stops_at_the_next_job(self):
        block = job_block(_WORKFLOW, "first")
        self.assertIn("name: First", block)
        self.assertNotIn("name: Second", block)

    def test_trailing_comments_belong_to_the_next_job(self):
        """Otherwise an assertion can be satisfied by prose written about the job below it."""
        self.assertNotIn("introduces `second`", job_block(_WORKFLOW, "first"))

    def test_last_job_runs_to_the_end_of_the_file(self):
        self.assertIn("echo ok", job_block(_WORKFLOW, "second"))

    def test_missing_job_is_an_error_naming_where_it_was_sought(self):
        with self.assertRaises(AssertionError) as caught:
            job_block(_WORKFLOW, "third", where="ci.yml")
        self.assertIn("third", str(caught.exception))
        self.assertIn("ci.yml", str(caught.exception))


class RunFanInTest(unittest.TestCase):
    def setUp(self):
        self.block = job_block(_WORKFLOW, "second")

    def test_result_is_substituted_into_the_script(self):
        self.assertEqual(run_fan_in(self.block, "first", "success").returncode, 0)
        self.assertEqual(run_fan_in(self.block, "first", "skipped").returncode, 1)

    def test_a_needs_name_the_script_does_not_read_is_an_error(self):
        """Left in place, `${{ ... }}` makes bash error and the script exit 0 — a false pass."""
        with self.assertRaises(AssertionError):
            run_fan_in(self.block, "mismatched", "success")

    def test_a_job_with_no_run_script_is_an_error(self):
        with self.assertRaises(AssertionError):
            run_fan_in("  third:\n    name: Third\n", "first", "success")


if __name__ == "__main__":
    unittest.main()
