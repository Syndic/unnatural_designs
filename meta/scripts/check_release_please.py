#!/usr/bin/env python3
"""
Verifies the release-please configuration is valid, internally consistent, and complete.

release-please drives per-component releases off two checked-in files — `release-please-config.json`
(what to release and how to tag it) and `.release-please-manifest.json` (each component's last
released version). This guard catches the ways those can silently rot, all offline (no network, no
node, no `release-please` invocation — the cheap structural checks only):

  1. Both files exist and parse as JSON; the config has a non-empty `packages` object.
  2. The config's package set and the manifest's key set are identical — release-please requires a
     manifest entry per package and refuses to run otherwise, and a manifest entry with no package
     is dead config.
  3. Every manifest version is valid semver (the string release-please appends `v...` to when it
     tags — a malformed one produces a malformed tag).
  4. Every configured package directory exists and carries a CHANGELOG.md (release-please appends to
     it; we seed it so the file is present from the first release and this check can be strict).
  5. Completeness — every releasable unit discovered on disk is a configured package. Today the
     releasable units are the repo's Go modules: they are consumed *directly from git tags*, and
     Go's module proxy only recognizes a subdirectory module's versions from tags shaped exactly
     `<module-path>/vX.Y.Z`. A Go module missing from the config would either go unreleased or, if
     released by a bare monorepo tag, advertise a bogus version to every downstream — the precise
     failure mode this whole setup exists to prevent.
  6. Go tag shape — each Go package resolves (per-package override else global else release-please
     default) to the exact tag settings that produce `<module-path>/vX.Y.Z`: component == the
     package path, `include-component-in-tag` true, `tag-separator` "/", `include-v-in-tag` true.
     Anything else yields a tag the Go toolchain will not resolve.

TEND(project-expand): the config's `packages` map enumerates the monorepo's releasable leaf units.
When a new releasable unit lands, add it to `release-please-config.json` and `.release-please-
manifest.json` and seed its CHANGELOG.md — this check fails until you do. If a unit is deliberately
never released, it needs an explicit opt-out here (an allowlist) rather than silent omission.

TEND(lang-expand): `releasable_units()` currently equates "releasable" with "Go module" because Go
is the only language whose artifacts are consumed straight from git tags. When a language ships
units that need independent versioning (e.g. a Python distribution published to an index, or a
container image), extend `releasable_units()` to discover them and teach `check_go_tag_shape()` /
the tag-shape expectations about that unit's tag convention (a non-Go unit won't want the Go
`path/vX.Y.Z` shape).

Usage: ./meta/scripts/check_release_please.py
"""

import json
import re
import sys
from pathlib import Path

# See check_modules.py for why the workspace root is put on sys.path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import col_range, find_go_modules, workspace_root

CONFIG_FILE = "release-please-config.json"
MANIFEST_FILE = ".release-please-manifest.json"

# Semver without the leading `v` — the form stored in the manifest. release-please prepends the
# `v` at tag time (include-v-in-tag), so the manifest holds the bare core + optional pre-release /
# build metadata. Deliberately stricter than "anything": a malformed entry here becomes a
# malformed tag.
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

# release-please defaults for the tag-shape keys, applied when neither the package nor the global
# config sets them. Kept beside the check so the fallback is auditable next to what it feeds.
# TEND(tooling): these mirror release-please's own defaults (include-component-in-tag=true,
# include-v-in-tag=true, tag-separator="-"). If a release-please upgrade changes a default, update
# it here so the effective-value resolution stays faithful.
_TAG_DEFAULTS = {
    "include-component-in-tag": True,
    "include-v-in-tag": True,
    "tag-separator": "-",
}


def _key_line(file: Path, key: str) -> int:
    """1-based line of the first occurrence of `"key"` in `file`, or 1 if not found.

    Lets package-specific diagnostics anchor on the package's line in the config/manifest rather
    than always at line 1, so an editor problem-matcher squiggles the right entry.
    """
    needle = f'"{key}"'
    try:
        for lineno, line in enumerate(file.read_text().splitlines(), start=1):
            if needle in line:
                return lineno
    except OSError:
        pass
    return 1


def _diag(file: Path, rel: str, key: str, message: str) -> str:
    """Format a `rel:line:start-end: message` diagnostic anchored on `key` within `file`."""
    line = _key_line(file, key)
    start, end = col_range(file, line, f'"{key}"')
    return f"{rel}:{line}:{start}-{end}: {message}"


def _load_json(path: Path, rel: str) -> tuple[dict | None, list[str]]:
    """Load and parse a JSON file, returning (data-or-None, errors)."""
    if not path.is_file():
        return None, [f"{rel}:1:1-2: {rel} missing"]
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return None, [f"{rel}:{e.lineno}:{max(e.colno, 1)}-{e.colno + 1}: invalid JSON ({e.msg})"]
    if not isinstance(data, dict):
        return None, [f"{rel}:1:1-2: {rel} must be a JSON object"]
    return data, []


def _effective(config: dict, pkg_cfg: dict, key: str):
    """Resolve a tag-shape setting: per-package override, else global, else release-please default."""
    if key in pkg_cfg:
        return pkg_cfg[key]
    if key in config:
        return config[key]
    return _TAG_DEFAULTS[key]


def check_manifest_matches_packages(
    config: dict, manifest: dict, config_path: Path, manifest_path: Path
) -> list[str]:
    """Package set (config) and key set (manifest) must be identical — release-please requires it."""
    packages = set(config.get("packages", {}))
    versions = set(manifest)
    errors = []
    for pkg in sorted(packages - versions):
        errors.append(
            _diag(config_path, CONFIG_FILE, pkg, f"package '{pkg}' has no entry in {MANIFEST_FILE}")
        )
    for pkg in sorted(versions - packages):
        errors.append(
            _diag(
                manifest_path,
                MANIFEST_FILE,
                pkg,
                f"manifest entry '{pkg}' has no package in {CONFIG_FILE}",
            )
        )
    return errors


def check_manifest_versions(manifest: dict, manifest_path: Path) -> list[str]:
    """Every manifest version must be valid semver (no leading `v`)."""
    errors = []
    for pkg, version in sorted(manifest.items()):
        if not isinstance(version, str) or not _SEMVER.match(version):
            errors.append(
                _diag(
                    manifest_path,
                    MANIFEST_FILE,
                    pkg,
                    f"version {version!r} for '{pkg}' is not valid semver (e.g. 1.2.3)",
                )
            )
    return errors


def check_package_dirs_and_changelogs(root: Path, config: dict, config_path: Path) -> list[str]:
    """Every configured package directory must exist and carry a CHANGELOG.md."""
    errors = []
    for pkg in sorted(config.get("packages", {})):
        pkg_dir = root / pkg
        if not pkg_dir.is_dir():
            errors.append(_diag(config_path, CONFIG_FILE, pkg, f"package dir ./{pkg} does not exist"))
            continue
        if not (pkg_dir / "CHANGELOG.md").is_file():
            errors.append(
                f"{pkg}/CHANGELOG.md:1:1-2: missing CHANGELOG.md for release-please package ./{pkg}"
            )
    return errors


def check_completeness(root: Path, config: dict, config_path: Path) -> list[str]:
    """Every releasable unit on disk must be a configured package (anti-drift completeness)."""
    configured = set(config.get("packages", {}))
    errors = []
    for unit in sorted(releasable_units(root) - configured):
        # Anchor on the config file's `packages` key — the missing entry has no line of its own yet.
        errors.append(
            _diag(
                config_path,
                CONFIG_FILE,
                "packages",
                f"releasable unit ./{unit} is not a release-please package "
                f"(add it to {CONFIG_FILE} + {MANIFEST_FILE} and seed its CHANGELOG.md)",
            )
        )
    return errors


def check_go_tag_shape(root: Path, config: dict, config_path: Path) -> list[str]:
    """Each Go package must resolve to tag settings producing `<module-path>/vX.Y.Z`."""
    go_modules = {str(p) for p in find_go_modules(root)}
    packages = config.get("packages", {})
    errors = []
    for pkg in sorted(set(packages) & go_modules):
        pkg_cfg = packages[pkg] if isinstance(packages[pkg], dict) else {}
        expected = {
            "component": pkg,
            "include-component-in-tag": True,
            "tag-separator": "/",
            "include-v-in-tag": True,
        }
        # `component` has no global fallback in release-please — it is per-package only.
        actual = {"component": pkg_cfg.get("component")} | {
            k: _effective(config, pkg_cfg, k) for k in _TAG_DEFAULTS
        }
        for key, want in expected.items():
            if actual[key] != want:
                errors.append(
                    _diag(
                        config_path,
                        CONFIG_FILE,
                        pkg,
                        f"Go package '{pkg}': {key} resolves to {actual[key]!r}, "
                        f"needs {want!r} so the tag is '{pkg}/vX.Y.Z' (Go rejects other shapes)",
                    )
                )
    return errors


def releasable_units(root: Path) -> set[Path]:
    """The set of units that must be release-please packages. See the module-level TEND(lang-expand).

    Today: the repo's Go modules — the only artifacts consumed directly from git tags, where the
    tag *is* the version. Returned as `str`-comparable relative paths matching the config's package
    keys.
    """
    return {str(p) for p in find_go_modules(root)}


def main() -> int:
    root = workspace_root()
    config_path = root / CONFIG_FILE
    manifest_path = root / MANIFEST_FILE

    config, errors = _load_json(config_path, CONFIG_FILE)
    manifest, manifest_errors = _load_json(manifest_path, MANIFEST_FILE)
    errors += manifest_errors

    # The remaining checks need both files parsed and a `packages` object; bail early otherwise so a
    # parse failure produces one clear diagnostic rather than a cascade.
    if config is not None and not isinstance(config.get("packages"), dict):
        errors.append(f"{CONFIG_FILE}:1:1-2: missing or non-object 'packages'")
        config = None

    if config is not None and manifest is not None:
        errors += check_manifest_matches_packages(config, manifest, config_path, manifest_path)
        errors += check_manifest_versions(manifest, manifest_path)
        errors += check_package_dirs_and_changelogs(root, config, config_path)
        errors += check_completeness(root, config, config_path)
        errors += check_go_tag_shape(root, config, config_path)

    for line in errors:
        print(line)
    if not errors:
        print("release-please config is valid, consistent, and complete.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
