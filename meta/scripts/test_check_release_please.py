"""Tests for check_release_please.py.

The check functions take already-parsed config/manifest dicts plus a filesystem root, so most
tests build a small dict and a tempdir rather than a full repo. Disk-touching checks (changelog
presence, completeness, Go tag shape) get a temp tree with go.mod / CHANGELOG.md as needed.
"""

import json
import tempfile
import unittest
from pathlib import Path

from meta.scripts import check_release_please
from meta.scripts.check_release_please import (
    CONFIG_FILE,
    MANIFEST_FILE,
    _effective,
    _load_json,
    check_completeness,
    check_go_tag_shape,
    check_manifest_matches_packages,
    check_manifest_versions,
    check_package_dirs_and_changelogs,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_go_module(root: Path, rel_path: str, with_changelog: bool = True) -> None:
    mod_dir = root / rel_path
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "go.mod").write_text(
        f"module github.com/Syndic/unnatural_designs/{rel_path}\ngo 1.26.1\n"
    )
    if with_changelog:
        (mod_dir / "CHANGELOG.md").write_text("# Changelog\n")


def go_package(component: str) -> dict:
    """A per-package config block that (with the global tag defaults) yields a Go-valid tag."""
    return {"release-type": "go", "component": component}


GLOBAL_TAG_CONFIG = {
    "include-component-in-tag": True,
    "include-v-in-tag": True,
    "tag-separator": "/",
}


def write_json(root: Path, name: str, data: object) -> Path:
    path = root / name
    path.write_text(json.dumps(data, indent=2))
    return path


# ── _load_json ───────────────────────────────────────────────────────────────


class LoadJsonTest(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, errors = _load_json(Path(tmp) / CONFIG_FILE, CONFIG_FILE)
            self.assertIsNone(data)
            self.assertIn("missing", errors[0])

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / CONFIG_FILE
            path.write_text("{ not json ")
            data, errors = _load_json(path, CONFIG_FILE)
            self.assertIsNone(data)
            self.assertIn("invalid JSON", errors[0])

    def test_non_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), CONFIG_FILE, [1, 2, 3])
            data, errors = _load_json(path, CONFIG_FILE)
            self.assertIsNone(data)
            self.assertIn("must be a JSON object", errors[0])

    def test_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), CONFIG_FILE, {"packages": {}})
            data, errors = _load_json(path, CONFIG_FILE)
            self.assertEqual(data, {"packages": {}})
            self.assertEqual(errors, [])


# ── _effective ───────────────────────────────────────────────────────────────


class EffectiveTest(unittest.TestCase):
    def test_per_package_wins(self):
        cfg = {"tag-separator": "/"}
        self.assertEqual(_effective(cfg, {"tag-separator": "-"}, "tag-separator"), "-")

    def test_global_over_default(self):
        self.assertEqual(_effective({"tag-separator": "/"}, {}, "tag-separator"), "/")

    def test_default_when_unset(self):
        # release-please's default separator is "-".
        self.assertEqual(_effective({}, {}, "tag-separator"), "-")


# ── manifest ↔ packages bijection ────────────────────────────────────────────


class ManifestMatchTest(unittest.TestCase):
    def test_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"a": {}, "b": {}}})
            man_path = write_json(root, MANIFEST_FILE, {"a": "1.0.0", "b": "0.1.0"})
            config = json.loads(cfg_path.read_text())
            manifest = json.loads(man_path.read_text())
            self.assertEqual(
                check_manifest_matches_packages(config, manifest, cfg_path, man_path), []
            )

    def test_package_without_manifest_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"a": {}, "b": {}}})
            man_path = write_json(root, MANIFEST_FILE, {"a": "1.0.0"})
            errors = check_manifest_matches_packages(
                json.loads(cfg_path.read_text()), json.loads(man_path.read_text()), cfg_path, man_path
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("'b' has no entry", errors[0])

    def test_manifest_entry_without_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"a": {}}})
            man_path = write_json(root, MANIFEST_FILE, {"a": "1.0.0", "ghost": "2.0.0"})
            errors = check_manifest_matches_packages(
                json.loads(cfg_path.read_text()), json.loads(man_path.read_text()), cfg_path, man_path
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("'ghost' has no package", errors[0])


# ── manifest versions ────────────────────────────────────────────────────────


class ManifestVersionsTest(unittest.TestCase):
    def test_valid_semver(self):
        with tempfile.TemporaryDirectory() as tmp:
            man_path = write_json(Path(tmp), MANIFEST_FILE, {"a": "1.2.3", "b": "0.0.0-rc.1"})
            self.assertEqual(
                check_manifest_versions(json.loads(man_path.read_text()), man_path), []
            )

    def test_rejects_leading_v(self):
        with tempfile.TemporaryDirectory() as tmp:
            man_path = write_json(Path(tmp), MANIFEST_FILE, {"a": "v1.2.3"})
            errors = check_manifest_versions(json.loads(man_path.read_text()), man_path)
            self.assertEqual(len(errors), 1)
            self.assertIn("not valid semver", errors[0])

    def test_rejects_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            man_path = write_json(Path(tmp), MANIFEST_FILE, {"a": "1.2"})
            errors = check_manifest_versions(json.loads(man_path.read_text()), man_path)
            self.assertEqual(len(errors), 1)


# ── package dirs + changelogs ─────────────────────────────────────────────────


class PackageDirsTest(unittest.TestCase):
    def test_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"tools/foo": {}}})
            config = json.loads(cfg_path.read_text())
            self.assertEqual(check_package_dirs_and_changelogs(root, config, cfg_path), [])

    def test_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"tools/ghost": {}}})
            config = json.loads(cfg_path.read_text())
            errors = check_package_dirs_and_changelogs(root, config, cfg_path)
            self.assertEqual(len(errors), 1)
            self.assertIn("does not exist", errors[0])

    def test_missing_changelog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo", with_changelog=False)
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"tools/foo": {}}})
            config = json.loads(cfg_path.read_text())
            errors = check_package_dirs_and_changelogs(root, config, cfg_path)
            self.assertEqual(len(errors), 1)
            self.assertIn("CHANGELOG.md", errors[0])


# ── completeness ─────────────────────────────────────────────────────────────


class CompletenessTest(unittest.TestCase):
    def test_all_modules_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"tools/foo": {}}})
            config = json.loads(cfg_path.read_text())
            self.assertEqual(check_completeness(root, config, cfg_path), [])

    def test_unconfigured_module_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "tools/bar")
            cfg_path = write_json(root, CONFIG_FILE, {"packages": {"tools/foo": {}}})
            config = json.loads(cfg_path.read_text())
            errors = check_completeness(root, config, cfg_path)
            self.assertEqual(len(errors), 1)
            self.assertIn("tools/bar", errors[0])
            self.assertIn("not a release-please package", errors[0])


# ── Go tag shape ─────────────────────────────────────────────────────────────


class GoTagShapeTest(unittest.TestCase):
    def _run(self, root: Path, packages: dict, global_cfg: dict | None = None) -> list[str]:
        cfg = {**(global_cfg or {}), "packages": packages}
        cfg_path = write_json(root, CONFIG_FILE, cfg)
        return check_go_tag_shape(root, json.loads(cfg_path.read_text()), cfg_path)

    def test_correct_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            errors = self._run(root, {"tools/foo": go_package("tools/foo")}, GLOBAL_TAG_CONFIG)
            self.assertEqual(errors, [])

    def test_wrong_component(self):
        # component defaulting to the basename ("foo") instead of the full path is the classic Go
        # mis-tag; it must be flagged.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            errors = self._run(root, {"tools/foo": go_package("foo")}, GLOBAL_TAG_CONFIG)
            self.assertTrue(any("component" in e for e in errors))

    def test_wrong_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            # No global tag config → separator defaults to "-", which is wrong for Go.
            errors = self._run(root, {"tools/foo": go_package("tools/foo")})
            self.assertTrue(any("tag-separator" in e for e in errors))

    def test_component_in_tag_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            pkg = go_package("tools/foo") | {"include-component-in-tag": False}
            errors = self._run(root, {"tools/foo": pkg}, GLOBAL_TAG_CONFIG)
            self.assertTrue(any("include-component-in-tag" in e for e in errors))


# ── end-to-end main() on the real repo ───────────────────────────────────────


class MainTest(unittest.TestCase):
    def test_repo_config_is_valid(self):
        """The checked-in config must itself pass — this is the guard guarding itself."""
        self.assertEqual(check_release_please.main(), 0)


if __name__ == "__main__":
    unittest.main()
