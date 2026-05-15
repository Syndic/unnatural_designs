"""Tests for check_no_cgo.py.

Source-level (`find_cgo_in_sources`) and orchestration (`check`, `main`) logic is unit-
tested here. The `find_cgo_in_deps` subprocess wrapper is exercised by mocking
`subprocess.run` — its argument construction, env, output parsing, and error path are
covered, but the real `go list` invocation against a fixture module is left to the CI
job (`no-cgo-check` in .github/workflows/ci.yml), which runs the script end-to-end with
a real Go toolchain."""

import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meta.scripts import check_no_cgo
from meta.scripts.check_no_cgo import find_cgo_in_sources


def write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class TestFindCgoInSources(unittest.TestCase):

    def test_no_go_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_cgo_in_sources(Path(tmp)), [])

    def test_go_file_without_cgo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", 'package foo\n\nimport "fmt"\n')
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_detects_import_c(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                '/*\n'
                '#include <stdio.h>\n'
                '*/\n'
                'import "C"\n'
            ))
            self.assertEqual(find_cgo_in_sources(root), [Path("pkg/foo.go")])

    def test_detects_import_c_with_other_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                'import (\n'
                '    "fmt"\n'
                ')\n'
                '\n'
                'import "C"\n'
            ))
            self.assertEqual(find_cgo_in_sources(root), [Path("pkg/foo.go")])

    def test_does_not_match_string_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                'var s = `import "C"`\n'  # backtick string, not a top-level import
            ))
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_does_not_match_quoted_in_doc_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                '// We do not use `import "C"` here.\n'
                'import "fmt"\n'
            ))
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_finds_across_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "a/foo.go", 'package a\nimport "C"\n')
            write(root, "b/bar.go", 'package b\nimport "C"\n')
            offenders = sorted(find_cgo_in_sources(root))
            self.assertEqual(offenders, [Path("a/foo.go"), Path("b/bar.go")])

    def test_excludes_bazel_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "bazel-out/pkg/foo.go", 'package foo\nimport "C"\n')
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_excludes_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".git/hooks/foo.go", 'package foo\nimport "C"\n')
            self.assertEqual(find_cgo_in_sources(root), [])


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestFindCgoInDeps(unittest.TestCase):

    def test_empty_output_returns_empty_list(self):
        with mock.patch.object(subprocess, "run", return_value=_completed(stdout="")) as m:
            self.assertEqual(check_no_cgo.find_cgo_in_deps(Path("/tmp/m")), [])
            args, kwargs = m.call_args
            cmd = args[0]
            self.assertEqual(cmd[:4], ["go", "list", "-deps", "-f"])
            self.assertEqual(cmd[-1], "./...")
            self.assertIn("CgoFiles", cmd[4])
            self.assertIn("SysoFiles", cmd[4])
            self.assertEqual(kwargs["cwd"], Path("/tmp/m"))
            self.assertEqual(kwargs["env"]["CGO_ENABLED"], "1")
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])

    def test_parses_offender_lines(self):
        out = "github.com/foo/cgolib\ngithub.com/bar/swiglib\n"
        with mock.patch.object(subprocess, "run", return_value=_completed(stdout=out)):
            self.assertEqual(
                check_no_cgo.find_cgo_in_deps(Path("/tmp/m")),
                ["github.com/foo/cgolib", "github.com/bar/swiglib"],
            )

    def test_strips_whitespace_and_blank_lines(self):
        out = "  github.com/foo/cgolib  \n\n   \ngithub.com/bar/swiglib\n"
        with mock.patch.object(subprocess, "run", return_value=_completed(stdout=out)):
            self.assertEqual(
                check_no_cgo.find_cgo_in_deps(Path("/tmp/m")),
                ["github.com/foo/cgolib", "github.com/bar/swiglib"],
            )

    def test_nonzero_exit_raises(self):
        with mock.patch.object(subprocess, "run", return_value=_completed(stderr="boom", returncode=1)):
            with self.assertRaises(RuntimeError) as cm:
                check_no_cgo.find_cgo_in_deps(Path("/tmp/m"))
            self.assertIn("boom", str(cm.exception))
            self.assertIn("/tmp/m", str(cm.exception))


class TestCheck(unittest.TestCase):

    def _patches(self, *, sources=None, deps_by_module=None, modules=("tools/foo",), go_present=True):
        sources = sources or []
        deps_by_module = deps_by_module or {}

        def fake_find_cgo_in_deps(module_dir: Path):
            return deps_by_module.get(module_dir.name, [])

        return [
            mock.patch.object(check_no_cgo, "find_cgo_in_sources", return_value=[Path(p) for p in sources]),
            mock.patch.object(check_no_cgo, "find_cgo_in_deps", side_effect=fake_find_cgo_in_deps),
            mock.patch.object(check_no_cgo, "registered_modules", return_value={Path(m) for m in modules}),
            mock.patch.object(check_no_cgo.shutil, "which", return_value="/usr/bin/go" if go_present else None),
        ]

    def _run(self, *, root=Path("/tmp/repo"), **kwargs):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            patches = self._patches(**kwargs)
            for p in patches:
                p.start()
            try:
                rc = check_no_cgo.check(root)
            finally:
                for p in patches:
                    p.stop()
            return rc, stdout.getvalue(), stderr.getvalue()

    def test_clean_returns_zero(self):
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)
        self.assertIn("1 Go module(s)", out)

    def test_no_modules_message(self):
        rc, out, _ = self._run(modules=())
        self.assertEqual(rc, 0)
        self.assertIn("no Go modules", out)

    def test_source_offender_fails(self):
        rc, out, _ = self._run(sources=["pkg/foo.go"])
        self.assertEqual(rc, 1)
        self.assertIn("pkg/foo.go", out)
        self.assertIn("repo source files", out)
        self.assertIn("future-considerations.md", out)

    def test_dep_offender_fails(self):
        rc, out, _ = self._run(deps_by_module={"foo": ["github.com/foo/cgolib"]})
        self.assertEqual(rc, 1)
        self.assertIn("github.com/foo/cgolib", out)
        self.assertIn("//tools/foo", out)

    def test_source_and_dep_offenders_both_reported(self):
        rc, out, _ = self._run(
            sources=["pkg/x.go"],
            deps_by_module={"foo": ["github.com/foo/cgolib"]},
        )
        self.assertEqual(rc, 1)
        self.assertIn("pkg/x.go", out)
        self.assertIn("github.com/foo/cgolib", out)

    def test_runtime_error_in_deps_check_fails(self):
        def explode(module_dir: Path):
            raise RuntimeError("simulated `go list` failure")
        with mock.patch.object(check_no_cgo, "find_cgo_in_sources", return_value=[]), \
             mock.patch.object(check_no_cgo, "find_cgo_in_deps", side_effect=explode), \
             mock.patch.object(check_no_cgo, "registered_modules", return_value={Path("tools/foo")}), \
             mock.patch.object(check_no_cgo.shutil, "which", return_value="/usr/bin/go"), \
             mock.patch("sys.stdout", new_callable=io.StringIO), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            rc = check_no_cgo.check(Path("/tmp/repo"))
        self.assertEqual(rc, 1)
        self.assertIn("simulated `go list` failure", stderr.getvalue())

    def test_go_missing_fails_loudly(self):
        rc, out, err = self._run(go_present=False)
        self.assertEqual(rc, 1)
        self.assertIn("error", err)
        self.assertIn("not on PATH", err)
        self.assertIn("Install Go", err)
        self.assertNotIn("OK", out)


class TestMain(unittest.TestCase):

    def test_main_delegates_to_check_at_workspace_root(self):
        with mock.patch.object(check_no_cgo, "workspace_root", return_value=Path("/fake/root")) as wr, \
             mock.patch.object(check_no_cgo, "check", return_value=0) as ck:
            self.assertEqual(check_no_cgo.main(), 0)
            wr.assert_called_once_with()
            ck.assert_called_once_with(Path("/fake/root"))

    def test_main_propagates_exit_code(self):
        with mock.patch.object(check_no_cgo, "workspace_root", return_value=Path("/fake/root")), \
             mock.patch.object(check_no_cgo, "check", return_value=1):
            self.assertEqual(check_no_cgo.main(), 1)


if __name__ == "__main__":
    unittest.main()
