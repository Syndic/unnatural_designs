"""Load the shared path-classification rule sets from `.github/path-rules.toml`.

Private shared helper, like `_workspace.py`: the consumers are `classify_changed_paths.py` and
`base_image_pin_hook.py`, and the whole reason it exists is that they must not each carry their
own copy of which paths feed the devcontainer base image.

`tomllib` is stdlib, so this stays importable from a bare `python3` with no dependency — which
matters because the classifier runs in the job that produces a required check's inputs, before
anything has set up `uv`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


class RuleError(ValueError):
    """A rules file that cannot be turned into a usable set of patterns."""


def _resolve(name: str, sets: dict[str, dict], chain: tuple[str, ...]) -> list[str]:
    """Patterns for `name`: its own first, then each included set's, deduped in that order."""
    if name in chain:
        raise RuleError(f"include cycle: {' -> '.join([*chain, name])}")
    try:
        spec = sets[name]
    except KeyError:
        raise RuleError(f"no such rule set: {name!r}") from None

    patterns = list(spec.get("patterns", []))
    for included in spec.get("include", []):
        patterns.extend(_resolve(included, sets, (*chain, name)))
    if not patterns:
        raise RuleError(f"rule set {name!r} has no patterns and includes nothing")

    # Dedupe preserving order: two included sets may legitimately share a pattern, and the
    # alternation should not repeat it.
    return list(dict.fromkeys(patterns))


def load_rules(path: Path | str) -> dict[str, str]:
    """Map every rule set name in `path` to one alternation regex.

    Composition is flattened here rather than at the call sites, so a consumer never needs to
    know that `base` is `bazel` plus a directory.
    """
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    sets = data.get("rules")
    if not sets:
        raise RuleError(f"no [rules.*] sets in {path}")

    resolved = {name: "|".join(_resolve(name, sets, ())) for name in sets}
    for name, pattern in resolved.items():
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleError(f"rule set {name!r} does not compile: {exc}") from exc
    return resolved


def select(rules: dict[str, str], names: list[str]) -> dict[str, str]:
    """The named subset, in the order asked for. Unknown names are an error, not an empty group."""
    missing = [n for n in names if n not in rules]
    if missing:
        raise RuleError(f"no such rule set(s): {', '.join(sorted(missing))}")
    return {name: rules[name] for name in names}
