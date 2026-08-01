"""Tests for the wiring between devcontainer.json, the Dockerfile, and the base image.

Three couplings live across those files and none of them fails loudly:

  - The `BASE_IMAGE` override has an exact working shape. Every nearby shape either breaks
    every local `devcontainer up` (an empty `--build-arg` overriding the Dockerfile default)
    or silently truncates the digest (a substitution default is split on `:`).
  - The pinned base carries both a tag and a digest, because Renovate needs a tag to compare
    against and the digest is what makes the build reproducible.
  - The lifecycle hooks call the command the base image installs. Calling the workspace copy
    instead still works here — the scripts are checked in — so the mistake would only surface
    in Syndic/.dotfiles, which has no such copy.

The rationale for all three is in meta/devcontainer-base/README.md, "Consuming the image".
The parsing helpers are pure so they can be exercised directly, same split as the shell tests
in this directory.
"""

import json
import re
import sys
import unittest
from pathlib import Path

# Not .resolve(): devcontainer.yml is a cross-package data dep and lives in the runfiles tree,
# which a resolved symlink would lead back out of. The rest read fine either way.
_HERE = Path(__file__).parent
_DEVCONTAINER_JSON = _HERE / "devcontainer.json"
_DOCKERFILE = _HERE / "Dockerfile"
_HOOKS = (_HERE / "post-create.sh", _HERE / "post-start.sh")
_DEVCONTAINER_WORKFLOW = _HERE.parent / ".github" / "workflows" / "devcontainer.yml"

# The host-side name CI sets and devcontainer.json reads. Namespaced on purpose — see
# test_the_host_variable_is_the_namespaced_one.
_HOST_ENV_VAR = "DEVCONTAINER_BASE_IMAGE"

# Where the base image installs the dispatcher. Consumers call it by absolute path rather
# than by name so a PATH surprise fails visibly instead of finding some other file.
_PLUMBING_COMMAND = "/usr/local/bin/devcontainer-plumbing"
_BASE_REPOSITORY = "ghcr.io/syndic/unnatural_designs-devcontainer-base"

_LOCAL_ENV_RE = re.compile(
    r"\A\$\{localEnv:(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?::(?P<default>.*))?\}\Z"
)


def scan_outside_comments(text: str):
    """Yield `(char, in_string)` for every character of `text` that is not in a comment.

    `in_string` covers the quotes themselves, so a `//` inside a JSON string value is data
    and a `,` inside one is never mistaken for a trailing comma.
    """
    i, n, in_string = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_string:
            yield ch, True
            if ch == "\\" and i + 1 < n:
                yield text[i + 1], True
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            yield ch, True
            i += 1
            continue
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        yield ch, False
        i += 1


def strip_jsonc(text: str) -> str:
    """Turn JSONC (comments + trailing commas, which devcontainer.json uses) into JSON."""
    chars = list(scan_outside_comments(text))

    # Next significant character strictly after each index, precomputed in one reverse pass:
    # a trailing comma is one whose next significant character closes a container. Whitespace
    # *inside* a string stays significant, so a `,` in a string value is never a candidate.
    next_significant: list[str | None] = [None] * len(chars)
    nxt: str | None = None
    for idx in range(len(chars) - 1, -1, -1):
        next_significant[idx] = nxt
        ch, in_string = chars[idx]
        if in_string or not ch.isspace():
            nxt = ch

    return "".join(
        ch
        for idx, (ch, in_string) in enumerate(chars)
        if not (ch == "," and not in_string and next_significant[idx] in ("}", "]"))
    )


def parse_local_env(value: str) -> tuple[str, str | None]:
    """Split a `${localEnv:NAME}` / `${localEnv:NAME:default}` substitution.

    Returns `(name, default)` with `default` None when the substitution carries none — the
    case that passes an empty `--build-arg` and overrides the Dockerfile's own default.
    """
    match = _LOCAL_ENV_RE.match(value)
    if not match:
        raise ValueError(f"not a localEnv substitution: {value!r}")
    return match.group("var"), match.group("default")


def dockerfile_instructions(text: str) -> list[tuple[str, str]]:
    """Ordered instruction *heads* — `(FIRST_WORD, rest)` per non-comment, non-blank line.

    Not a Dockerfile parser: `\\`-continuation lines come back as their own entries (a `&& ...`
    head). That is enough for the FROM/ARG identity and ordering questions asked below, none of
    which spans a continuation, and it keeps the indices monotonic in file order.
    """
    instructions = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head, _, rest = stripped.partition(" ")
        instructions.append((head.upper(), rest.strip()))
    return instructions


def parse_from(argument: str) -> tuple[str, str | None]:
    """Split a FROM argument into `(image, stage_alias)`."""
    parts = argument.split()
    if len(parts) >= 3 and parts[1].upper() == "AS":
        return parts[0], parts[2]
    return parts[0], None


class TestJsoncReader(unittest.TestCase):
    """The reader the config assertions stand on; a lenient one would make them vacuous."""

    def test_line_comment_is_dropped(self):
        self.assertEqual(json.loads(strip_jsonc('{\n// note\n"a": 1\n}')), {"a": 1})

    def test_block_comment_is_dropped(self):
        self.assertEqual(json.loads(strip_jsonc('{/* note */"a": 1}')), {"a": 1})

    def test_double_slash_inside_a_string_survives(self):
        # The failure this prevents: eating the rest of the line from a URL or an image
        # reference, which turns a real assertion into a parse error or a missing key.
        self.assertEqual(
            json.loads(strip_jsonc('{"a": "https://example.test/x", "b": 1}')),
            {"a": "https://example.test/x", "b": 1},
        )

    def test_trailing_comma_is_dropped(self):
        self.assertEqual(json.loads(strip_jsonc('{"a": [1, 2,],}')), {"a": [1, 2]})

    def test_comma_inside_a_string_before_a_brace_survives(self):
        self.assertEqual(json.loads(strip_jsonc('{"a": "x,}"}')), {"a": "x,}"})

    def test_escaped_quote_does_not_end_the_string(self):
        self.assertEqual(
            json.loads(strip_jsonc(r'{"a": "he said \"//\"", "b": 2}')),
            {"a": 'he said "//"', "b": 2},
        )


class TestLocalEnvParsing(unittest.TestCase):
    def test_default_is_returned(self):
        self.assertEqual(parse_local_env("${localEnv:X:fallback}"), ("X", "fallback"))

    def test_missing_default_is_none(self):
        self.assertEqual(parse_local_env("${localEnv:X}"), ("X", None))

    def test_empty_default_is_distinct_from_missing(self):
        self.assertEqual(parse_local_env("${localEnv:X:}"), ("X", ""))

    def test_non_substitution_rejected(self):
        with self.assertRaises(ValueError):
            parse_local_env("plain-value")


class TestBaseImageOverride(unittest.TestCase):
    """The three-line Dockerfile shape and the substitution that selects between its halves."""

    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(strip_jsonc(_DEVCONTAINER_JSON.read_text(encoding="utf-8")))
        cls.instructions = dockerfile_instructions(_DOCKERFILE.read_text(encoding="utf-8"))
        cls.froms = [
            (idx, *parse_from(arg))
            for idx, (kind, arg) in enumerate(cls.instructions)
            if kind == "FROM"
        ]

    def _build_arg(self) -> str:
        return self.config["build"]["args"]["BASE_IMAGE"]

    def _global_arg_default(self) -> tuple[int, str]:
        for idx, (kind, arg) in enumerate(self.instructions):
            if kind == "ARG" and arg.startswith("BASE_IMAGE="):
                return idx, arg.split("=", 1)[1]
        raise AssertionError("Dockerfile declares no BASE_IMAGE ARG")

    def test_substitution_has_a_default(self):
        # Without one the CLI passes `--build-arg BASE_IMAGE=`, which overrides the
        # Dockerfile's ARG default with the empty string and breaks FROM for every local up.
        _, default = parse_local_env(self._build_arg())
        self.assertIsNotNone(default)
        self.assertNotEqual(default, "")

    def test_substitution_default_is_colon_free(self):
        # The CLI splits the substitution on `:` and takes field 2, so anything after a colon
        # is silently dropped — a digest, or even a plain `repo:tag`, cannot live here.
        _, default = parse_local_env(self._build_arg())
        self.assertNotIn(":", default)

    def test_the_host_variable_is_the_namespaced_one(self):
        # Deliberately not the build arg's own name: this one is read from the developer's
        # environment and goes into FROM unvalidated, so a stray generic `BASE_IMAGE` export
        # must not select their base image.
        name, _ = parse_local_env(self._build_arg())
        self.assertEqual(name, _HOST_ENV_VAR)

    def test_ci_sets_the_same_variable(self):
        # The two sides are a hand-coupling across files: a rename here without one in the
        # workflow leaves CI passing an env var nothing reads, and the override silently
        # degrades to the pinned digest — a green build that tested the wrong image.
        workflow = _DEVCONTAINER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(f"{_HOST_ENV_VAR}:", workflow)
        self.assertIn("devcontainer-base:ci", workflow)

    def test_arg_default_equals_the_substitution_default(self):
        # The two defaults are the same sentinel, so an unset BASE_IMAGE and an explicitly
        # passed `pinned-base` resolve to the same stage.
        _, arg_default = self._global_arg_default()
        _, substitution_default = parse_local_env(self._build_arg())
        self.assertEqual(arg_default, substitution_default)

    def test_arg_precedes_every_from(self):
        # An ARG after the first FROM is stage-scoped and invisible to FROM.
        arg_index, _ = self._global_arg_default()
        self.assertLess(arg_index, min(idx for idx, _, _ in self.froms))

    def test_sentinel_is_a_stage_alias_on_the_pinned_base(self):
        # `AS <sentinel>` is what makes Renovate treat the consuming FROM as an internal stage
        # reference and skip it; without the alias it emits `pinned-base` as a bogus dep.
        _, sentinel = self._global_arg_default()
        aliases = {alias: image for _, image, alias in self.froms if alias}
        self.assertIn(sentinel, aliases)
        self.assertTrue(aliases[sentinel].startswith(f"{_BASE_REPOSITORY}:"))

    def test_pinned_base_carries_both_a_tag_and_a_digest(self):
        # Digest for reproducibility, tag for Renovate to have something to compare against —
        # the same pairing MODULE.bazel's oci.pull uses for this image's own base.
        _, sentinel = self._global_arg_default()
        image = next(img for _, img, alias in self.froms if alias == sentinel)
        reference, _, digest = image.partition("@")
        self.assertRegex(digest, r"\Asha256:[0-9a-f]{64}\Z")
        self.assertRegex(reference, rf"\A{re.escape(_BASE_REPOSITORY)}:[\w][\w.-]*\Z")

    def test_alias_precedes_the_consuming_from(self):
        # Renovate's stage-name check only knows aliases declared above the line it is looking
        # at, so the order — not just the presence of the alias — is what keeps it quiet.
        _, sentinel = self._global_arg_default()
        alias_index = next(idx for idx, _, alias in self.froms if alias == sentinel)
        consuming_index = next(idx for idx, image, _ in self.froms if "${BASE_IMAGE}" in image)
        self.assertLess(alias_index, consuming_index)

    def test_the_last_stage_is_the_overridable_one(self):
        # The image the devcontainer runs is the final stage; if the pinned-base stage were
        # last, the override would resolve and then be ignored.
        _, image, _ = self.froms[-1]
        self.assertEqual(image, "${BASE_IMAGE}")


class TestHooksCallTheInstalledCommand(unittest.TestCase):
    def test_hooks_use_the_image_path(self):
        for hook in _HOOKS:
            with self.subTest(hook=hook.name):
                self.assertIn(_PLUMBING_COMMAND, hook.read_text(encoding="utf-8"))

    def test_hooks_do_not_call_the_workspace_copy(self):
        # The in-repo scripts are the image's *source*. Calling them directly works here and
        # nowhere else, so it would look correct until a second consumer adopted the image.
        for hook in _HOOKS:
            with self.subTest(hook=hook.name):
                self.assertNotIn("devcontainer-plumbing.sh", hook.read_text(encoding="utf-8"))

    def test_hooks_pass_both_locations_explicitly(self):
        # The command lives outside the workspace, so it cannot derive either from $0; its
        # cwd-based defaults are a fallback the consumer should not be relying on.
        for hook in _HOOKS:
            with self.subTest(hook=hook.name):
                text = hook.read_text(encoding="utf-8")
                self.assertIn("PLUMBING_WORKSPACE=", text)
                self.assertIn("PLUMBING_DIR=", text)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
