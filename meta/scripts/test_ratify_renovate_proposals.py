"""Tests for ratify_renovate_proposals.py.

The pure functions (extract_proposals, parse_resolved, find_conflicts, git_diff_args)
carry all the non-I/O logic; tests focus there. main()'s driver wiring is exercised
end-to-end by the caller workflow on a real Renovate PR — covering it here would mean
mocking subprocess heavily for low marginal value.
"""

import textwrap
import unittest

from meta.scripts.ratify_renovate_proposals import (
    Proposal,
    extract_proposals,
    find_conflicts,
    git_diff_args,
    parse_resolved,
)
from packaging.utils import NormalizedName, canonicalize_name


def _resolved(pins: dict[str, str]) -> dict[NormalizedName, str]:
    """A hand-written resolved map, keyed as `parse_resolved` keys its own."""
    return {canonicalize_name(name): version for name, version in pins.items()}


class TestExtractProposals(unittest.TestCase):
    def test_empty_diff(self):
        self.assertEqual(extract_proposals(""), [])

    def test_no_pin_lines(self):
        diff = textwrap.dedent("""\
            diff --git a/requirements_lock.txt b/requirements_lock.txt
            index abc..def 100644
            --- a/requirements_lock.txt
            +++ b/requirements_lock.txt
            @@ -1,3 +1,3 @@
             # comment line that didn't change
        """)
        self.assertEqual(extract_proposals(diff), [])

    def test_single_package_bump(self):
        diff = textwrap.dedent("""\
            @@ -1,4 +1,4 @@
            -certifi==2026.5.20 \\
            -    --hash=sha256:abc
            +certifi==2026.6.17 \\
            +    --hash=sha256:def
        """)
        self.assertEqual(
            extract_proposals(diff),
            [Proposal(name="certifi", target="2026.6.17")],
        )

    def test_multi_hash_continuation_lines_ignored(self):
        # uv export writes one `--hash=sha256:...` continuation per artifact. The diff
        # for a bump shows several `+` continuation lines per package; none of them
        # should be mistaken for proposals.
        diff = textwrap.dedent("""\
            -certifi==2026.5.20 \\
            -    --hash=sha256:111 \\
            -    --hash=sha256:222
            +certifi==2026.6.17 \\
            +    --hash=sha256:333 \\
            +    --hash=sha256:444 \\
            +    --hash=sha256:555
        """)
        self.assertEqual(
            extract_proposals(diff),
            [Proposal(name="certifi", target="2026.6.17")],
        )

    def test_comment_continuation_lines_ignored(self):
        # uv export annotates each package with `    # via <parent>`. Renovate's diff
        # may include those as `+` lines if the parent package name changed.
        diff = textwrap.dedent("""\
            +certifi==2026.6.17 \\
            +    --hash=sha256:abc
            +    # via requests
        """)
        self.assertEqual(
            extract_proposals(diff),
            [Proposal(name="certifi", target="2026.6.17")],
        )

    def test_file_header_lines_ignored(self):
        # `+++ b/requirements_lock.txt` starts with `+` but is the file header, not a
        # pin. The `extract_proposals` filter must exclude it.
        diff = textwrap.dedent("""\
            --- a/requirements_lock.txt
            +++ b/requirements_lock.txt
            @@ -1,4 +1,4 @@
            -certifi==2026.5.20
            +certifi==2026.6.17
        """)
        self.assertEqual(
            extract_proposals(diff),
            [Proposal(name="certifi", target="2026.6.17")],
        )

    def test_multiple_packages_sorted_by_name(self):
        # A Renovate group may bump multiple packages in one PR (e.g. several pypi deps
        # under a grouping rule). The output is sorted so downstream consumers get
        # deterministic ordering — easier to reason about and to test against.
        diff = textwrap.dedent("""\
            -urllib3==2.6.0
            +urllib3==2.7.1
            -certifi==2026.5.20
            +certifi==2026.6.17
            -idna==3.17
            +idna==3.18
        """)
        self.assertEqual(
            extract_proposals(diff),
            [
                Proposal(name="certifi", target="2026.6.17"),
                Proposal(name="idna", target="3.18"),
                Proposal(name="urllib3", target="2.7.1"),
            ],
        )

    def test_packages_with_dots_dashes_underscores(self):
        # PyPI names regularly include `.`, `-`, `_`. Confirm the regex doesn't reject.
        diff = textwrap.dedent("""\
            +charset-normalizer==3.4.7
            +python_dateutil==2.9.0
            +ruamel.yaml==0.18.6
        """)
        self.assertEqual(
            [p.name for p in extract_proposals(diff)],
            ["charset-normalizer", "python_dateutil", "ruamel.yaml"],
        )

    def test_keeps_renovate_spelling(self):
        # `uv lock --upgrade-package` and the diagnostics get the name as Renovate wrote it.
        diff = "+PyYAML==6.0.4\n"
        self.assertEqual(extract_proposals(diff), [Proposal(name="PyYAML", target="6.0.4")])

    def test_sorted_by_canonical_name(self):
        # Lexical order would put `Zope` before `attrs`; canonical order does not.
        diff = "+Zope==5.0\n+attrs==24.1.0\n"
        self.assertEqual([p.name for p in extract_proposals(diff)], ["attrs", "Zope"])

    def test_spellings_of_one_package_keep_the_highest_target(self):
        # Both line orders: last-wins would drop the 0.18.6 bump when it comes first.
        for diff in (
            "+ruamel.yaml==0.18.5\n+ruamel_yaml==0.18.6\n",
            "+ruamel_yaml==0.18.6\n+ruamel.yaml==0.18.5\n",
        ):
            with self.subTest(diff=diff):
                self.assertEqual(
                    extract_proposals(diff),
                    [Proposal(name="ruamel_yaml", target="0.18.6")],
                )


class TestParseResolved(unittest.TestCase):
    def test_empty_file(self):
        self.assertEqual(parse_resolved(""), {})

    def test_header_and_comment_lines_ignored(self):
        text = textwrap.dedent("""\
            # This file was autogenerated by uv
            #    uv export ...
            certifi==2026.5.20 \\
                --hash=sha256:abc
                # via requests
        """)
        self.assertEqual(parse_resolved(text), {"certifi": "2026.5.20"})

    def test_realistic_multi_package_lockfile(self):
        text = textwrap.dedent("""\
            # header
            certifi==2026.5.20 \\
                --hash=sha256:abc \\
                --hash=sha256:def
                # via requests
            charset-normalizer==3.4.7 \\
                --hash=sha256:ghi
                # via requests
            urllib3==2.7.0 \\
                --hash=sha256:jkl
                # via requests
        """)
        self.assertEqual(
            parse_resolved(text),
            {
                "certifi": "2026.5.20",
                "charset-normalizer": "3.4.7",
                "urllib3": "2.7.0",
            },
        )

    def test_last_occurrence_wins_on_duplicate(self):
        # uv export shouldn't produce duplicates, but if upstream ever does we want a
        # stable, documented behavior (last write wins — closest to "what a user
        # editing the file by hand would expect"). Not load-bearing today; documenting
        # so a future regression is visible.
        text = "certifi==2026.5.20\ncertifi==2026.6.17\n"
        self.assertEqual(parse_resolved(text), {"certifi": "2026.6.17"})

    def test_keys_are_canonical_names(self):
        text = "PyYAML==6.0.4\nruamel.yaml==0.18.6\nPython_Dateutil==2.9.0\n"
        self.assertEqual(
            parse_resolved(text),
            {"pyyaml": "6.0.4", "ruamel-yaml": "0.18.6", "python-dateutil": "2.9.0"},
        )


class TestFindConflicts(unittest.TestCase):
    def test_no_proposals_no_conflicts(self):
        self.assertEqual(
            find_conflicts([], _resolved({"certifi": "2026.5.20"})),
            [],
        )

    def test_all_honored_exact_match(self):
        proposals = [Proposal(name="certifi", target="2026.6.17")]
        resolved = _resolved({"certifi": "2026.6.17"})
        self.assertEqual(find_conflicts(proposals, resolved), [])

    def test_honored_when_uv_picks_newer_than_proposed(self):
        # Real case: a newer version shipped between Renovate's scan and our uv lock,
        # so uv resolves to the freshest. Renovate proposed 2026.6.17, uv resolved
        # 2026.7.1 — still an advance in spirit. Not a conflict.
        proposals = [Proposal(name="certifi", target="2026.6.17")]
        resolved = _resolved({"certifi": "2026.7.1"})
        self.assertEqual(find_conflicts(proposals, resolved), [])

    def test_conflict_when_uv_kept_old_version(self):
        # The headline failure mode: workspace constraint refuses to advance, uv keeps
        # the existing version, Renovate's proposed bump is unachievable.
        proposals = [Proposal(name="certifi", target="2026.6.17")]
        resolved = _resolved({"certifi": "2026.5.20"})
        conflicts = find_conflicts(proposals, resolved)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("certifi", conflicts[0])
        self.assertIn("2026.5.20", conflicts[0])
        self.assertIn("2026.6.17", conflicts[0])
        self.assertIn("would not advance", conflicts[0])

    def test_conflict_when_package_missing_from_resolved(self):
        # Edge case: uv lock removed the package entirely (e.g. it was a transitive
        # whose parent dep was dropped). The diagnostic should still surface what
        # Renovate wanted so the maintainer can decide.
        proposals = [Proposal(name="ghost", target="1.2.3")]
        resolved = _resolved({})
        conflicts = find_conflicts(proposals, resolved)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("ghost", conflicts[0])
        self.assertIn("not present", conflicts[0])
        self.assertIn("1.2.3", conflicts[0])

    def test_mix_of_honored_and_unhonored(self):
        # Realistic group-bump case: some packages advance cleanly, others don't.
        # The script's job is to surface only the unhonored ones.
        proposals = [
            Proposal(name="certifi", target="2026.6.17"),
            Proposal(name="urllib3", target="2.7.1"),
        ]
        resolved = _resolved({"certifi": "2026.6.17", "urllib3": "2.7.0"})
        conflicts = find_conflicts(proposals, resolved)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("urllib3", conflicts[0])
        self.assertNotIn("certifi", conflicts[0])

    def test_pep440_pre_release_comparison(self):
        # pep440 ordering can be unintuitive vs. lexical compare — a pre-release sorts
        # before its corresponding final. Confirms we're using packaging.version.Version
        # and not strcmp.
        proposals = [Proposal(name="pkg", target="2.0.0")]
        resolved = _resolved({"pkg": "2.0.0a1"})  # pre-release < final
        conflicts = find_conflicts(proposals, resolved)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("2.0.0a1", conflicts[0])

    def test_pep440_post_release_honored(self):
        # Post-releases (e.g. 2.0.0.post1) sort AFTER their base. If Renovate proposed
        # 2.0.0 and uv resolved 2.0.0.post1, that's an advance.
        proposals = [Proposal(name="pkg", target="2.0.0")]
        resolved = _resolved({"pkg": "2.0.0.post1"})
        self.assertEqual(find_conflicts(proposals, resolved), [])

    def test_pep440_version_with_epoch(self):
        # Epoch prefixes (`1!2.0.0`) are unusual but legal in pep440 — they sort above
        # any version with a lower epoch regardless of the rest of the string.
        proposals = [Proposal(name="pkg", target="9999.0.0")]
        resolved = _resolved({"pkg": "1!0.0.1"})  # epoch 1 > epoch 0 (no epoch)
        self.assertEqual(find_conflicts(proposals, resolved), [])


class TestNameSpellings(unittest.TestCase):
    """Renovate and `uv export` can spell one package differently; PEP 503 says they match.

    Each case runs the real pipeline: a Renovate diff line through `extract_proposals`, a
    `uv export` line through `parse_resolved`, then `find_conflicts`.
    """

    def _conflicts(self, proposed: str, exported: str) -> list[str]:
        return find_conflicts(extract_proposals(f"+{proposed}\n"), parse_resolved(f"{exported}\n"))

    def test_case_difference_honored(self):
        # #386: Renovate wrote the PyPI display name, uv exports the normalized one.
        self.assertEqual(self._conflicts("PyYAML==6.0.4", "pyyaml==6.0.4"), [])

    def test_dot_vs_dash_honored(self):
        self.assertEqual(self._conflicts("ruamel.yaml==0.18.6", "ruamel-yaml==0.18.6"), [])

    def test_underscore_vs_dash_honored(self):
        self.assertEqual(self._conflicts("ruamel_yaml==0.18.6", "ruamel-yaml==0.18.6"), [])

    def test_run_of_separators_honored(self):
        self.assertEqual(self._conflicts("Foo._-Bar==1.0", "foo-bar==1.0"), [])

    def test_unhonored_bump_still_conflicts_under_renovate_spelling(self):
        conflicts = self._conflicts("PyYAML==6.0.4", "pyyaml==6.0.3")
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].startswith("PyYAML: uv resolved 6.0.3"), conflicts[0])

    def test_distinct_packages_are_not_merged(self):
        # Canonicalization folds case and separators only; a different name is still absent.
        conflicts = self._conflicts("PyYAML==6.0.4", "pyyaml-include==6.0.4")
        self.assertEqual(len(conflicts), 1)
        self.assertIn("not present", conflicts[0])


class TestGitDiffArgs(unittest.TestCase):
    def test_uses_three_dot_range(self):
        # Two-dot (`origin/main`) diffs the base tip, so a base that advanced under an open
        # PR reads as if the PR reverted it — the `+` lines become the branch's older pins
        # and extract_proposals mistakes them for Renovate's proposals.
        args = git_diff_args("main", "requirements_lock.txt")
        self.assertEqual(args, ["git", "diff", "origin/main...HEAD", "--", "requirements_lock.txt"])

    def test_path_is_passed_after_separator(self):
        # The `--` matters: a lockfile whose name collides with a ref would otherwise be
        # ambiguous to git.
        args = git_diff_args("release/2.x", "sub/dir/requirements_lock.txt")
        self.assertEqual(args[-2], "--")
        self.assertEqual(args[-1], "sub/dir/requirements_lock.txt")
        self.assertEqual(args[2], "origin/release/2.x...HEAD")


if __name__ == "__main__":
    unittest.main()
