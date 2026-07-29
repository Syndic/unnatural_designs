"""Tests for devcontainer_lock_drift.py.

The pure functions (find_drift, find_major_available, format_summary, format_outputs) carry all
the logic; tests focus there. The `devcontainer outdated` invocation and $GITHUB_OUTPUT wiring are
exercised end-to-end by devcontainer-lock.yml on its schedule.

REPORT_* fixtures use the real field names and shape the CLI emits (verified against
@devcontainers/cli 0.88.0), so a schema change upstream surfaces here rather than as a workflow
that silently stops finding drift.
"""

import unittest

from meta.scripts.devcontainer_lock_drift import (
    find_drift,
    find_major_available,
    format_outputs,
    format_summary,
)

# Everything current — the steady state the schedule hits most weeks.
REPORT_CURRENT = {
    "ghcr.io/devcontainers/features/common-utils:2": {
        "current": "2.5.9",
        "wanted": "2.5.9",
        "wantedMajor": "2",
        "latest": "2.5.9",
        "latestMajor": "2",
    },
    "ghcr.io/devcontainers/features/go:1": {
        "current": "1.3.4",
        "wanted": "1.3.4",
        "wantedMajor": "1",
        "latest": "1.3.4",
        "latestMajor": "1",
    },
}

# The real drift this workflow was built for: the state main sat in before #211 refreshed it.
REPORT_DRIFTED = {
    "ghcr.io/devcontainers/features/common-utils:2": {
        "current": "2.5.8",
        "wanted": "2.5.9",
        "wantedMajor": "2",
        "latest": "2.5.9",
        "latestMajor": "2",
    },
    "ghcr.io/devcontainers/features/git:1": {
        "current": "1.3.5",
        "wanted": "1.3.8",
        "wantedMajor": "1",
        "latest": "1.3.8",
        "latestMajor": "1",
    },
    "ghcr.io/devcontainers/features/go:1": {
        "current": "1.3.4",
        "wanted": "1.3.4",
        "wantedMajor": "1",
        "latest": "1.3.4",
        "latestMajor": "1",
    },
}

# Pinned at :2 while 3.x exists. `upgrade` cannot cross the major, so this is a note, not drift.
REPORT_MAJOR_BEHIND = {
    "ghcr.io/devcontainers/features/common-utils:2": {
        "current": "2.5.9",
        "wanted": "2.5.9",
        "wantedMajor": "2",
        "latest": "3.1.0",
        "latestMajor": "3",
    },
}


class TestFindDrift(unittest.TestCase):
    def test_no_drift_when_current(self):
        self.assertEqual(find_drift(REPORT_CURRENT), [])

    def test_finds_each_drifted_feature(self):
        self.assertEqual(
            find_drift(REPORT_DRIFTED),
            [
                ("ghcr.io/devcontainers/features/common-utils:2", "2.5.8", "2.5.9"),
                ("ghcr.io/devcontainers/features/git:1", "1.3.5", "1.3.8"),
            ],
        )

    def test_major_behind_is_not_drift(self):
        # `upgrade` would move nothing here, so opening a PR would produce an empty diff.
        self.assertEqual(find_drift(REPORT_MAJOR_BEHIND), [])

    def test_empty_report(self):
        self.assertEqual(find_drift({}), [])

    def test_missing_fields_are_skipped_not_guessed(self):
        # A feature the CLI could not resolve is not evidence of drift.
        self.assertEqual(find_drift({"x": {"current": "1.0.0"}}), [])
        self.assertEqual(find_drift({"x": {"wanted": "1.0.0"}}), [])
        self.assertEqual(find_drift({"x": {}}), [])

    def test_sorted_by_feature_name(self):
        # Stable ordering keeps the PR body diff-free when nothing actually changed.
        report = {
            "zzz": {"current": "1", "wanted": "2"},
            "aaa": {"current": "1", "wanted": "2"},
        }
        self.assertEqual([n for n, _, _ in find_drift(report)], ["aaa", "zzz"])


class TestFindMajorAvailable(unittest.TestCase):
    def test_none_when_on_latest_major(self):
        self.assertEqual(find_major_available(REPORT_CURRENT), [])

    def test_finds_major_gap(self):
        self.assertEqual(
            find_major_available(REPORT_MAJOR_BEHIND),
            [("ghcr.io/devcontainers/features/common-utils:2", "2", "3")],
        )

    def test_patch_drift_is_not_a_major_gap(self):
        self.assertEqual(find_major_available(REPORT_DRIFTED), [])

    def test_missing_fields_are_skipped(self):
        self.assertEqual(find_major_available({"x": {"wantedMajor": "2"}}), [])


class TestFormatSummary(unittest.TestCase):
    def test_lists_each_drifted_feature_as_a_table_row(self):
        summary = format_summary(find_drift(REPORT_DRIFTED), [])
        self.assertIn(
            "| `ghcr.io/devcontainers/features/common-utils:2` | 2.5.8 | 2.5.9 |", summary
        )
        self.assertIn("| `ghcr.io/devcontainers/features/git:1` | 1.3.5 | 1.3.8 |", summary)

    def test_omits_major_note_when_there_is_none(self):
        # Match the note's opening, not the bare word "major" — the table's intro line
        # ("within each pinned major") contains that too.
        self.assertNotIn("A newer **major**", format_summary(find_drift(REPORT_DRIFTED), []))

    def test_includes_major_note_when_present(self):
        summary = format_summary(
            find_drift(REPORT_DRIFTED), find_major_available(REPORT_MAJOR_BEHIND)
        )
        self.assertIn("Renovate", summary)
        self.assertIn("`3` available", summary)


class TestFormatOutputs(unittest.TestCase):
    def test_drift_true_when_drift(self):
        out = format_outputs(find_drift(REPORT_DRIFTED), "body")
        self.assertTrue(out.startswith("drift=true\n"))

    def test_drift_false_when_current(self):
        self.assertTrue(format_outputs([], "body").startswith("drift=false\n"))

    def test_multiline_summary_uses_heredoc_form(self):
        # A bare `summary=<multiline>` would break $GITHUB_OUTPUT parsing after the first line.
        out = format_outputs([], "line one\nline two")
        self.assertIn("summary<<SUMMARY_EOF\nline one\nline two\nSUMMARY_EOF\n", out)


if __name__ == "__main__":
    unittest.main()
