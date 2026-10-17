import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml
from meta.scripts import check_composite_action_scripts as cca
from meta.scripts._workflows import composite_action_files

_ROOT = Path(__file__).parent.parent.parent


def action(steps: str) -> str:
    return "runs:\n  using: composite\n  steps:\n" + textwrap.indent(textwrap.dedent(steps), "    ")


class ScriptsTestCase(unittest.TestCase):
    def scripts(self, text: str) -> tuple[list[cca.Script], list[tuple[int, str]]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "action.yml"
            path.write_text(text, encoding="utf-8")
            return cca.scripts(path)

    def one(self, text: str) -> cca.Script:
        (script,), problems = self.scripts(text)
        self.assertEqual(problems, [])
        return script


class TestLocate(ScriptsTestCase):
    def body_start(self, text: str, script: cca.Script) -> str:
        return text.splitlines()[script.first_line - 1][script.column - 1 :]

    def test_a_literal_block_starts_on_the_line_after_its_indicator(self):
        text = action("- shell: bash\n  run: |\n    set -u\n    echo hi\n")
        script = self.one(text)
        self.assertTrue(script.exact)
        self.assertEqual(self.body_start(text, script), "set -u")

    def test_a_one_line_plain_value_starts_at_its_mark(self):
        text = action("- shell: bash\n  run: echo hi\n")
        script = self.one(text)
        self.assertTrue(script.exact)
        self.assertEqual(self.body_start(text, script), "echo hi")

    def test_a_one_line_quoted_value_starts_after_its_quote(self):
        text = action("- shell: bash\n  run: 'echo hi'\n")
        script = self.one(text)
        self.assertTrue(script.exact)
        self.assertEqual(self.body_start(text, script), "echo hi'")

    def test_an_escape_in_a_quoted_value_makes_it_inexact(self):
        self.assertFalse(self.one(action('- shell: bash\n  run: "echo \\"hi\\""\n')).exact)

    def test_a_folded_value_is_inexact(self):
        text = action("- shell: bash\n  run: >\n    echo\n    hi\n")
        script = self.one(text)
        self.assertFalse(script.exact)
        self.assertEqual(text.splitlines()[script.run_line - 1].strip(), "run: >")

    def test_expressions_leave_a_literal_block_exact(self):
        # body_position undoes actionlint's masking, so only the scalar's style decides.
        text = action("- shell: bash\n  run: |\n    echo ${{ 'é'\n      }}\n    echo $b\n")
        self.assertTrue(self.one(text).exact)


class TestBodyPosition(unittest.TestCase):
    def test_a_body_without_expressions_is_unchanged(self):
        self.assertEqual(cca.body_position("echo a\necho $b\n", 2, 6), (2, 6))

    def test_a_line_after_a_two_line_expression_is_renumbered(self):
        # actionlint masks the newline inside the expression, so `$b` is on its script line 2.
        self.assertEqual(cca.body_position("echo ${{ a\n  }}\necho $b\n", 2, 6), (3, 6))

    def test_a_column_after_a_multi_line_expression_on_the_same_line(self):
        body = 'echo "${{ a\n}}" $x'
        masked_column = len('echo "') + len("${{ a\n}}") + len('" $')  # 1-based column of `$`
        self.assertEqual(cca.body_position(body, 1, masked_column), (2, 5))

    def test_a_non_ascii_expression_is_counted_in_characters(self):
        # `${{ 'é' }}` is ten characters but eleven bytes, and actionlint masks bytes.
        self.assertEqual(cca.body_position("echo ${{ 'é' }} $x", 1, 18), (1, 17))

    def test_a_position_inside_an_expression_maps_to_its_start(self):
        self.assertEqual(cca.body_position("echo ${{ a }}", 1, 9), (1, 6))

    def test_a_position_past_the_body_is_returned_unchanged(self):
        self.assertEqual(cca.body_position("echo", 4, 2), (4, 2))


class TestShells(ScriptsTestCase):
    def test_a_custom_bash_invocation_lints_as_bash(self):
        self.assertEqual(self.one(action("- shell: bash -e {0}\n  run: echo hi\n")).shell, "bash")

    def test_sh_lints_as_sh(self):
        self.assertEqual(self.one(action("- shell: sh\n  run: echo hi\n")).shell, "sh")

    def test_a_non_shell_language_is_skipped(self):
        self.assertEqual(self.scripts(action("- shell: python\n  run: print(1)\n")), ([], []))

    def test_an_unrecognised_shell_is_a_problem_not_a_skip(self):
        scripts, problems = self.scripts(action("- shell: zsh\n  run: echo hi\n"))
        self.assertEqual(scripts, [])
        self.assertEqual([line for line, _ in problems], [5])

    def test_a_missing_shell_is_a_problem(self):
        # GitHub rejects a composite `run:` with no `shell:`, so it would never run either.
        scripts, problems = self.scripts(action("- run: echo hi\n"))
        self.assertEqual((scripts, len(problems)), ([], 1))

    def test_uses_steps_yield_nothing(self):
        self.assertEqual(self.scripts(action("- uses: actions/checkout@abc\n")), ([], []))

    def test_unparseable_yaml_is_a_problem(self):
        scripts, problems = self.scripts("runs: [\n")
        self.assertEqual((scripts, len(problems)), ([], 1))


class TestWrapperWorkflow(unittest.TestCase):
    def test_it_runs_the_body_unchanged_under_the_same_shell(self):
        script = cca.Script(5, 6, 9, True, "sh", 'echo "${{ inputs.x }}"\nexit 0\n')
        loaded = yaml.safe_load(cca.wrapper_workflow(script))
        (step,) = loaded["jobs"]["lint"]["steps"]
        self.assertEqual(step, {"shell": "sh", "run": script.body})


def finding(line: int, column: int, kind: str = "shellcheck") -> dict:
    message = f"shellcheck reported issue in this script: SC2086:info:{line}:{column}: Quote it"
    return {"kind": kind, "message": message}


class TestFormatFindings(unittest.TestCase):
    rel = Path("a/action.yml")

    def test_an_exact_script_adds_its_line_and_indentation(self):
        script = cca.Script(5, 10, 9, True, "bash", "echo a\necho $b\n")
        # Script line 2 is file line 11; column 6 shifts by the 8-space indent.
        self.assertEqual(
            cca.format_findings(script, self.rel, [finding(2, 6)]),
            ["a/action.yml:11:14-15: SC2086 (info): Quote it"],
        )

    def test_an_inexact_script_reports_at_the_run_key_and_keeps_the_body_position(self):
        script = cca.Script(5, 6, 1, False, "bash", "echo a\necho $b\n")
        self.assertEqual(
            cca.format_findings(script, self.rel, [finding(2, 6)]),
            ["a/action.yml:5:1-2: SC2086 (info): Quote it (`run:` line 2, column 6)"],
        )

    def test_an_inexact_position_after_a_two_line_expression_is_corrected(self):
        script = cca.Script(5, 6, 1, False, "bash", "echo ${{ a\n  }}\necho $b\n")
        self.assertEqual(
            cca.format_findings(script, self.rel, [finding(2, 6)]),
            ["a/action.yml:5:1-2: SC2086 (info): Quote it (`run:` line 3, column 6)"],
        )

    def test_an_exact_position_after_a_two_line_expression_is_corrected(self):
        script = cca.Script(5, 10, 9, True, "bash", "echo ${{ a\n  }}\necho $b\n")
        self.assertEqual(
            cca.format_findings(script, self.rel, [finding(2, 6)]),
            ["a/action.yml:12:14-15: SC2086 (info): Quote it"],
        )

    def test_other_rules_are_dropped(self):
        script = cca.Script(5, 10, 9, True, "bash", "")
        self.assertEqual(cca.format_findings(script, self.rel, [finding(1, 1, "expression")]), [])

    def test_a_shellcheck_message_it_cannot_parse_is_kept(self):
        script = cca.Script(5, 10, 9, True, "bash", "")
        error = {"kind": "shellcheck", "message": "something new"}
        self.assertEqual(
            cca.format_findings(script, self.rel, [error]), ["a/action.yml:5:1-2: something new"]
        )


class TestVersions(unittest.TestCase):
    def test_the_pin_is_read_without_its_v_and_located(self):
        dockerfile = "FROM x\n# renovate: ...\nARG SHELLCHECK_VERSION=v1.2.3\n"
        self.assertEqual(cca.pinned_version(dockerfile, "shellcheck"), cca.Pin("1.2.3", 3, 30))
        self.assertIsNone(cca.pinned_version("ARG OTHER_VERSION=v1.2.3\n", "shellcheck"))

    def test_a_mismatch_points_at_the_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".devcontainer").mkdir()
            (root / ".devcontainer" / "Dockerfile").write_text(
                "FROM x\nARG SHELLCHECK_VERSION=v1.2.3\nARG ACTIONLINT_VERSION=v4.5.6\n"
            )
            with mock.patch.object(cca.subprocess, "run", side_effect=FileNotFoundError):
                problems = cca.tool_problems(root)
        self.assertEqual(
            problems,
            [
                ".devcontainer/Dockerfile:2:1-30: shellcheck on PATH is missing, "
                "but the pin is 1.2.3",
                ".devcontainer/Dockerfile:3:1-30: actionlint on PATH is missing, "
                "but the pin is 4.5.6",
            ],
        )

    def test_both_tools_version_output_is_read(self):
        shellcheck = "ShellCheck - shell script analysis tool\nversion: 0.11.0\nlicense: GPL\n"
        actionlint = "1.7.12\ninstalled by downloading from release page\n"
        self.assertEqual(cca.reported_version(shellcheck), "0.11.0")
        self.assertEqual(cca.reported_version(actionlint), "1.7.12")
        self.assertIsNone(cca.reported_version(""))


class TestAgainstThisRepo(unittest.TestCase):
    """Held against the real composite actions, through the same selector `check` uses. The root
    comes from `__file__` because the Bazel sandbox has no git repo for `workspace_root()` to ask.
    """

    def test_every_composite_action_run_step_is_lintable(self):
        actions = composite_action_files(_ROOT)
        # Without the //:composite_actions data dep the directory is absent from the runfiles tree.
        self.assertTrue(actions, "no composite actions in the runfiles tree; this test is vacuous")
        found = 0
        for path in actions:
            with self.subTest(action=path.parent.name):
                scripts, problems = cca.scripts(path)
                self.assertEqual(problems, [])
                found += len(scripts)
        self.assertGreater(found, 0, "no shell run: steps found; the gate would check nothing")

    def test_the_actionlint_job_runs_this_check_after_the_pinned_tools(self):
        workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["actionlint"]["steps"]
        name = Path(cca.__file__).name
        (run,) = [i for i, s in enumerate(steps) if name in s.get("run", "")]
        installed = [
            s.get("with", {}).get("tool")
            for s in steps[:run]
            if s.get("uses") == "./.github/actions/install-pinned-release-binary"
        ]
        self.assertEqual(sorted(installed), ["actionlint", "shellcheck"])

    def test_the_check_and_its_setup_still_run_when_actionlint_fails(self):
        workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["actionlint"]["steps"]
        bare = next(i for i, s in enumerate(steps) if s.get("run", "").startswith("actionlint"))
        for step in steps[bare + 1 :]:
            with self.subTest(step=step.get("uses") or step.get("run")):
                self.assertEqual(step.get("if"), "${{ !cancelled() }}")


if __name__ == "__main__":
    unittest.main()
