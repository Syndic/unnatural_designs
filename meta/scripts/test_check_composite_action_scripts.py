import textwrap
import unittest
from pathlib import Path

import yaml
from meta.scripts import check_composite_action_scripts as sca
from meta.scripts._workflows import action_yaml_files

_ROOT = Path(__file__).parent.parent.parent


def action(steps: str) -> str:
    return "runs:\n  using: composite\n  steps:\n" + textwrap.indent(textwrap.dedent(steps), "    ")


class TestMaskExpressions(unittest.TestCase):
    def test_expressions_become_same_length_underscores(self):
        body = 'echo "${{ inputs.a }}" ${{ github.sha }}'
        masked = sca.mask_expressions(body)
        self.assertEqual(len(masked), len(body))
        self.assertEqual(masked, 'echo "' + "_" * 15 + '" ' + "_" * 17)

    def test_newlines_inside_an_expression_survive(self):
        self.assertEqual(sca.mask_expressions("${{ a\n}}"), "_____\n__")

    def test_plain_shell_is_untouched(self):
        self.assertEqual(
            sca.mask_expressions('echo "${HOME}" $((1 + 2))'), 'echo "${HOME}" $((1 + 2))'
        )


class TestRunScripts(unittest.TestCase):
    def test_a_literal_block_maps_to_the_line_after_its_indicator(self):
        text = action("""\
            - shell: bash
              run: |
                set -u
                echo hi
            """)
        (script,), problems = sca.run_scripts(text)
        self.assertEqual(problems, [])
        lines = text.splitlines()
        self.assertEqual(lines[script.first_line - 1].strip(), "set -u")
        self.assertEqual(lines[script.first_line - 1][script.column - 1 :], "set -u")
        self.assertTrue(script.exact)
        self.assertEqual(script.shell, "bash")

    def test_a_one_line_plain_value_maps_to_its_own_column(self):
        text = action("- shell: bash\n  run: echo hi\n")
        (script,), _ = sca.run_scripts(text)
        line = text.splitlines()[script.first_line - 1]
        self.assertEqual(line[script.column - 1 :], "echo hi")
        self.assertTrue(script.exact)

    def test_a_folded_value_is_reported_at_its_run_key(self):
        text = action("- shell: bash\n  run: >\n    echo\n    hi\n")
        (script,), _ = sca.run_scripts(text)
        self.assertFalse(script.exact)
        self.assertEqual(text.splitlines()[script.run_line - 1].strip(), "run: >")

    def test_a_custom_bash_invocation_lints_as_bash(self):
        (script,), _ = sca.run_scripts(action("- shell: bash -e {0}\n  run: echo hi\n"))
        self.assertEqual(script.shell, "bash")

    def test_sh_lints_as_sh(self):
        (script,), _ = sca.run_scripts(action("- shell: sh\n  run: echo hi\n"))
        self.assertEqual(script.shell, "sh")

    def test_a_non_shell_language_is_skipped(self):
        scripts, problems = sca.run_scripts(action("- shell: python\n  run: print(1)\n"))
        self.assertEqual((scripts, problems), ([], []))

    def test_an_unrecognised_shell_is_a_problem_not_a_skip(self):
        scripts, problems = sca.run_scripts(action("- shell: zsh\n  run: echo hi\n"))
        self.assertEqual(scripts, [])
        self.assertEqual([line for line, _ in problems], [5])

    def test_a_missing_shell_is_a_problem(self):
        # GitHub rejects a composite `run:` with no `shell:`, so nothing would be linted anyway.
        _, problems = sca.run_scripts(action("- run: echo hi\n"))
        self.assertEqual(len(problems), 1)

    def test_uses_steps_and_non_composite_files_yield_nothing(self):
        self.assertEqual(sca.run_scripts(action("- uses: actions/checkout@abc\n")), ([], []))
        self.assertEqual(sca.run_scripts("name: x\n"), ([], []))

    def test_unparseable_yaml_is_a_problem(self):
        scripts, problems = sca.run_scripts("runs: [\n")
        self.assertEqual(scripts, [])
        self.assertEqual(len(problems), 1)


class TestShellcheckInvocation(unittest.TestCase):
    script = sca.RunScript(5, 10, 9, True, "bash", "echo ${{ inputs.x }}\n")

    def test_input_is_the_runner_preamble_then_the_masked_body(self):
        self.assertEqual(
            sca.shellcheck_input(self.script), "set -eo pipefail\necho " + "_" * 15 + "\n"
        )

    def test_flags_read_stdin_with_no_rc_file(self):
        command = sca.shellcheck_command(self.script)
        self.assertEqual(command[0], "shellcheck")
        self.assertEqual(command[-1], "-")
        self.assertIn("--norc", command)
        self.assertEqual(command[command.index("--shell") + 1], "bash")


_COMMENT = {
    "line": 3,
    "column": 6,
    "endColumn": 8,
    "code": 2086,
    "level": "info",
    "message": "m",
}


class TestFormatFindings(unittest.TestCase):
    def test_an_exact_script_maps_past_the_preamble_and_indentation(self):
        script = sca.RunScript(5, 10, 9, True, "bash", "")
        # Input line 3 is body line 2, i.e. file line 11; column 6 shifts by the 8-space indent.
        self.assertEqual(
            sca.format_findings(script, Path("a/action.yml"), [_COMMENT]),
            ["a/action.yml:11:14-16: SC2086 (info): m"],
        )

    def test_an_inexact_script_reports_at_the_run_key(self):
        script = sca.RunScript(5, 6, 1, False, "bash", "")
        self.assertEqual(
            sca.format_findings(script, Path("a/action.yml"), [_COMMENT]),
            ["a/action.yml:5:1-2: SC2086 (info): m"],
        )


class TestAgainstThisRepo(unittest.TestCase):
    """Held against the real composite actions. The root comes from `__file__` because the Bazel
    sandbox has no git repo for `workspace_root()` to ask.
    """

    def test_every_composite_action_run_step_is_lintable(self):
        actions = [p for p in action_yaml_files(_ROOT) if p.parent.parent.name == "actions"]
        # Without the //:composite_actions data dep the directory is absent from the runfiles tree.
        self.assertTrue(actions, "no composite actions in the runfiles tree; this test is vacuous")
        found = 0
        for path in actions:
            with self.subTest(action=path.parent.name):
                scripts, problems = sca.run_scripts(path.read_text(encoding="utf-8"))
                self.assertEqual(problems, [])
                found += len(scripts)
        self.assertGreater(found, 0, "no shell run: steps found; the gate would check nothing")

    def test_the_actionlint_job_runs_this_check_with_shellcheck_installed(self):
        workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["actionlint"]["steps"]
        runs = [
            i for i, s in enumerate(steps) if sca.__name__.rsplit(".", 1)[1] in s.get("run", "")
        ]
        installs = [
            i
            for i, s in enumerate(steps)
            if s.get("uses") == "./.github/actions/install-pinned-release-binary"
            and s.get("with", {}).get("tool") == "shellcheck"
        ]
        self.assertEqual(len(runs), 1, "the actionlint job no longer runs this check")
        self.assertTrue(installs and installs[0] < runs[0], "shellcheck is not installed first")


if __name__ == "__main__":
    unittest.main()
