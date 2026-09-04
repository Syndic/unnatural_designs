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


def _repos() -> list[dict]:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))["repos"]


# The two halves of this file read deliberately different scopes. Documentation covers every hook,
# because the table is the reader's index of what runs on their commit and a remote hook runs there
# too. `language` covers local hooks only, because a remote repo's hooks are upstream-owned and
# this repo does not write their `language` to begin with.
def local_hooks() -> list[dict]:
    """Hook mappings under `repo: local`, in file order."""
    return [hook for repo in _repos() if repo["repo"] == "local" for hook in repo["hooks"]]


def config_hook_ids() -> list[str]:
    """Hook ids in .pre-commit-config.yaml, in file order, across every repo entry."""
    return [hook["id"] for repo in _repos() for hook in repo["hooks"]]


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


class HookLanguageTest(unittest.TestCase):
    """`language: unsupported` provisions nothing and runs `entry` against PATH, so a hook gets
    the interpreter the container pins. `language: python` instead builds a virtualenv and chooses
    its interpreter by searching PATH for a version-suffixed name, which `.python-version` is
    invisible to — so such a hook honours none of the repo's four Python pins, and any script
    importing `_workspace` breaks on the ≤3.13 it may land on.

    `unsupported`, not the `system` every pre-commit example still writes: `system` is not a
    language at all. `clientlib._translate_language` rewrites it to this one behind a shim
    upstream annotates `# remove`, and there is no `languages/system.py` to rewrite it to.
    Identical behaviour today, and the only spelling once the shim goes. This file reads the
    YAML directly rather than through pre-commit's loader, so it sees which one was written.
    """

    def setUp(self):
        self.hooks = local_hooks()
        # Nothing to disagree with is not agreement: an empty list would pass the assertion below.
        self.assertTrue(self.hooks, "no hooks found under `repo: local`")

    def test_every_local_hook_runs_on_the_containers_interpreter(self):
        wrong = {
            h["id"]: h.get("language") for h in self.hooks if h.get("language") != "unsupported"
        }
        self.assertEqual(
            wrong,
            {},
            "declare `language: unsupported` on these hooks. `system` is the deprecated alias "
            "for it that pre-commit's own docs still use — same behaviour, but it survives only "
            "while the translation shim does. Any other language makes pre-commit provision an "
            "interpreter of its own, ignoring the one this repo pins in .python-version",
        )


class PrecommitDocsTest(unittest.TestCase):
    def setUp(self):
        self.hooks = config_hook_ids()
        self.intro, self.table = readme_intro_and_table()
        # Still load-bearing after the parse: a config whose repos carry no hooks at all would
        # make every assertion below vacuous.
        self.assertTrue(self.hooks, "parsed no hooks out of .pre-commit-config.yaml")
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
