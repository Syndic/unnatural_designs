"""Holds `.pre-commit-config.yaml` to two properties: that README describes it, and that its
hooks run on the container's pinned interpreter.

The hook table and the paragraph above it both name hooks by id, and nothing else couples
them to the config — so a hook added, removed, or renamed leaves the prose stale with every
check still green. That is not hypothetical: the table sat two hooks behind the config, and
the paragraph's list of fixing hooks omitted one of them, for as long as nobody thought to
compare the two files.

Scope of the docs half is membership, order, and classification — not the "Triggers on" column.
That column paraphrases each hook's `files` regex for a reader; asserting a gloss against a
regex would either restate the regex in the README or accept anything.

The interpreter half is unrelated to the prose; its reasoning is at HookLanguageTest.
"""

import re
import unittest
from pathlib import Path

import yaml

# Not .resolve(): both files are cross-package data deps and live in the runfiles tree beside
# this one, which a resolved symlink would lead back out of.
_ROOT = Path(__file__).parent.parent.parent
_CONFIG = _ROOT / ".pre-commit-config.yaml"
_README = _ROOT / "README.md"

_TABLE_ROW_RE = re.compile(r"^\| `([^`]+)`\s*\|", re.M)

# Fenced blocks first: the install snippet's ``` fences are backticks too, and leaving them in
# offsets every inline-code pair after them into nonsense. The inline pattern then refuses to
# span newlines, so one stray backtick garbles a line rather than the rest of the paragraph.
_FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

# A backticked token shaped like a hook id. Hyphens are optional on purpose: half the ids are
# single words, so requiring kebab-case would let a removed `gazelle` or `shellcheck` sit in
# the prose unnoticed. What does the work is the exclusions — `go.mod`, `uv.lock`, `secrets/`,
# and the linked `.pre-commit-config.yaml` all appear in this paragraph and none is a hook.
_HOOK_SHAPED_RE = re.compile(r"\A[a-z][a-z0-9-]*\Z")

# Words that are hook-shaped but name something else. Strict by design: this paragraph exists
# to sort hooks into categories, so a bare backticked word in it reads as a hook id. Add here
# only when the prose genuinely needs one that isn't.
_NOT_A_HOOK = frozenset({"pre-commit"})


def hooks_in(text: str) -> list[dict]:
    """Every hook mapping in a pre-commit config, in file order, whatever repo declares it."""
    return [hook for repo in yaml.safe_load(text)["repos"] for hook in repo["hooks"]]


def config_hooks() -> list[dict]:
    """Every hook in .pre-commit-config.yaml, in file order."""
    return hooks_in(_CONFIG.read_text(encoding="utf-8"))


def config_hook_ids() -> list[str]:
    """Hook ids in .pre-commit-config.yaml, in file order."""
    return [hook["id"] for hook in config_hooks()]


def readme_intro_and_table() -> tuple[str, str]:
    """The prose between the **Pre-commit hooks** heading and the hook table, and the table."""
    text = _README.read_text(encoding="utf-8")
    start = text.index("**Pre-commit hooks**")
    header = text.index("| Hook", start)
    end = text.index("\n\n", header)
    return text[start:header], text[header:end]


def hook_shaped_names(text: str) -> set[str]:
    """Backticked tokens in `text` that read as hook ids."""
    return {
        token
        for token in _INLINE_CODE_RE.findall(_FENCE_RE.sub("", text))
        if _HOOK_SHAPED_RE.match(token) and token not in _NOT_A_HOOK
    }


class HookShapedNamesTest(unittest.TestCase):
    """The scan is what makes the README assertions non-vacuous, so it is tested directly."""

    def test_picks_out_hook_ids_and_ignores_everything_else(self):
        text = (
            "```\npre-commit install\n```\n\n"
            "`ruff-check` and `gazelle` run here, over `go.mod`, `uv.lock` and `secrets/`; "
            "see [`.pre-commit-config.yaml`](.pre-commit-config.yaml) and `pre-commit`."
        )
        self.assertEqual(hook_shaped_names(text), {"ruff-check", "gazelle"})

    def test_a_single_word_name_after_a_fence_is_still_seen(self):
        """The bug this guards: ``` fences offset the pairing and hid a stale mention."""
        self.assertEqual(hook_shaped_names("```\nx\n```\n\n`shellcheck` is gone."), {"shellcheck"})


class HooksInTest(unittest.TestCase):
    """Both halves of this file are vacuous if the extractor silently returns nothing, so it is
    tested directly rather than guarded on its output being non-empty. Same posture as
    HookShapedNamesTest, and it says what the guard could not: which hooks, in which order.
    """

    _FIXTURE = """\
repos:
  - repo: local
    hooks:
      - id: first
        language: unsupported
      - id: second
        language: unsupported
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: third
"""

    def test_collects_every_repos_hooks_in_file_order(self):
        self.assertEqual([h["id"] for h in hooks_in(self._FIXTURE)], ["first", "second", "third"])

    def test_a_remote_hook_carries_no_language_of_its_own(self):
        """Why the language assertion reads `.get`: the key is optional outside `repo: local`."""
        third = hooks_in(self._FIXTURE)[-1]
        self.assertIsNone(third.get("language"))


class HookLanguageTest(unittest.TestCase):
    """`language: unsupported` provisions nothing and runs `entry` against PATH, so a hook gets
    the interpreter the container pins. Every other language makes pre-commit build an environment
    and choose an interpreter itself — `python` searches PATH for a version-suffixed name, which
    `.python-version` is invisible to, so such a hook honours none of the repo's four Python pins
    and any script importing `_workspace` breaks on the ≤3.13 it may land on.

    Every hook, with no qualifier on where it comes from. `repo:` names where a hook's
    implementation lives, never what it checks, so it is the wrong axis for a property about
    which interpreter runs: a hook provisioning its own env does that whether this repo wrote it
    or vendored it. `language` is required on a local hook and optional on a remote one, so a
    remote entry that inherits its manifest's language fails here — correctly, since inheriting
    is precisely how a hook ends up off the container's interpreter.

    `unsupported`, not the `system` every pre-commit example still writes: `system` is not a
    language at all. `clientlib._translate_language` rewrites it to this one behind a shim
    upstream annotates `# remove`, and there is no `languages/system.py` to rewrite it to.
    Identical behaviour today, and the only spelling once the shim goes. This file reads the
    YAML directly rather than through pre-commit's loader, so it sees which one was written.
    """

    def test_every_hook_runs_on_the_containers_interpreter(self):
        # No non-vacuity guard: "no hook declares a bad language" is correctly true of a config
        # with no hooks, and HooksInTest covers the extractor that would have to break first.
        wrong = {
            h["id"]: h.get("language") for h in config_hooks() if h.get("language") != "unsupported"
        }
        self.assertEqual(
            wrong,
            {},
            "these hooks would not run on the interpreter this repo pins. A local hook should "
            "declare `language: unsupported` — `system` is the deprecated alias for it that "
            "pre-commit's own docs still use, and survives only while the translation shim does. "
            "A remote hook shown here with `None` inherits a language from its own manifest and "
            "has no place in this repo; vendor it as a local hook instead",
        )


class PrecommitDocsTest(unittest.TestCase):
    def setUp(self):
        self.hooks = config_hook_ids()
        self.intro, self.table = readme_intro_and_table()
        self.assertTrue(_TABLE_ROW_RE.findall(self.table), "parsed no rows out of the hook table")
        self.assertTrue(hook_shaped_names(self.intro), "parsed no hook names out of the prose")

    def test_table_lists_every_hook_in_config_order(self):
        """The table is the reader's index of the config; a drifted row sends them wrong."""
        self.assertEqual(
            _TABLE_ROW_RE.findall(self.table),
            self.hooks,
            "README's hook table disagrees with .pre-commit-config.yaml",
        )

    def test_intro_accounts_for_every_hook(self):
        """The prose sorts hooks into fixing / blocking; an unmentioned hook is unexplained."""
        missing = [hook for hook in self.hooks if f"`{hook}`" not in self.intro]
        self.assertEqual(
            missing, [], "hooks the README's pre-commit paragraph does not account for"
        )

    def test_intro_names_no_hook_the_config_lacks(self):
        """The mirror case: prose describing a hook that has been renamed or removed."""
        self.assertEqual(
            sorted(hook_shaped_names(self.intro) - set(self.hooks)),
            [],
            "names in the README's pre-commit paragraph that are not hooks in the config "
            "(if one of these is not meant to be a hook id, add it to _NOT_A_HOOK)",
        )


if __name__ == "__main__":
    unittest.main()
