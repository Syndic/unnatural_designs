"""Holds the one guard that reads outside the tree to its fail-closed promise.

Two halves, and the second is the one worth having. The comparison is a pure function and is
tested the ordinary way. The *retry policy* is where "fail closed" is either true or merely
claimed: a guard that swallowed a 403, or that treated an unreadable body as agreement, would go
green having checked nothing — the exact shape the manifest exists to stop one level up. So the
transport is exercised with an injected opener rather than described in a comment.
"""

from __future__ import annotations

import contextlib
import email.message
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from meta.scripts import check_repository_constraints
from meta.scripts.check_repository_constraints import (
    UnreadableRules,
    canonical,
    comparable,
    differences,
    fetch_rules,
)
from meta.scripts.ci_enforcement_manifest import RULES

_SOURCE = {"ruleset_source": "Syndic/unnatural_designs", "ruleset_source_type": "Repository"}


def _status_rule(*contexts: str, strict: bool = True) -> dict:
    return {
        "type": "required_status_checks",
        "parameters": {
            "strict_required_status_checks_policy": strict,
            "required_status_checks": [
                {"context": context, "integration_id": 15368} for context in contexts
            ],
        },
        **_SOURCE,
    }


class _Response:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _Opener:
    """An opener that replays a scripted sequence of outcomes, counting the calls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple):
            return _Response(*outcome)
        return _Response(outcome)


def _http_error(code: int) -> urllib.error.HTTPError:
    # `hdrs` is a Message rather than a mapping. Nothing here reads it, but an empty dict is the
    # wrong type and ty says so.
    return urllib.error.HTTPError(
        "https://api.github.com", code, "boom", email.message.Message(), None
    )


class CanonicalTest(unittest.TestCase):
    def test_list_order_does_not_survive_canonicalisation(self):
        self.assertEqual(canonical([3, 1, 2]), canonical([2, 3, 1]))

    def test_nested_lists_of_objects_are_ordered_too(self):
        one = [{"b": [2, 1]}, {"a": 1}]
        other = [{"a": 1}, {"b": [1, 2]}]
        self.assertEqual(canonical(one), canonical(other))

    def test_content_still_distinguishes(self):
        self.assertNotEqual(canonical([1, 2]), canonical([1, 3]))


class ComparableTest(unittest.TestCase):
    def test_ruleset_id_is_dropped(self):
        with_id = [dict(_status_rule("a"), ruleset_id=14538709)]
        self.assertEqual(comparable(with_id), comparable([_status_rule("a")]))

    def test_ruleset_source_is_kept(self):
        renamed = [dict(_status_rule("a"), ruleset_source="Syndic/elsewhere")]
        self.assertNotEqual(comparable(renamed), comparable([_status_rule("a")]))

    def test_integration_id_is_kept(self):
        rule = _status_rule("a")
        rule["parameters"]["required_status_checks"][0]["integration_id"] = 99
        self.assertNotEqual(comparable([rule]), comparable([_status_rule("a")]))

    def test_context_order_is_not_a_difference(self):
        self.assertEqual(comparable([_status_rule("a", "b")]), comparable([_status_rule("b", "a")]))

    def test_a_body_that_is_not_a_list_is_unreadable(self):
        with self.assertRaises(UnreadableRules):
            comparable({"message": "Not Found"})

    def test_a_rule_without_a_type_is_unreadable(self):
        with self.assertRaises(UnreadableRules):
            comparable([{"parameters": {}}])

    def test_a_rule_that_is_not_an_object_is_unreadable(self):
        with self.assertRaises(UnreadableRules):
            comparable(["required_status_checks"])

    def test_no_status_check_rule_is_unreadable_rather_than_agreement(self):
        """An empty answer is the shape a token without the permission produces."""
        with self.assertRaises(UnreadableRules):
            comparable([])
        with self.assertRaises(UnreadableRules):
            comparable([{"type": "deletion", **_SOURCE}])


class DifferencesTest(unittest.TestCase):
    def test_identical_rules_agree(self):
        rules = [_status_rule("a", "b")]
        self.assertEqual(differences(comparable(rules), comparable(rules)), [])

    def test_the_manifest_as_committed_agrees_with_itself(self):
        """Guards the mirror against being unreadable by its own comparison."""
        demanded = comparable(list(RULES))
        self.assertEqual(differences(demanded, demanded), [])

    def test_a_context_the_repository_dropped_is_reported(self):
        found = differences(comparable([_status_rule("a", "b")]), comparable([_status_rule("a")]))
        self.assertTrue(any("`b`" in message for message in found), found)

    def test_a_context_the_repository_added_is_reported(self):
        found = differences(comparable([_status_rule("a")]), comparable([_status_rule("a", "b")]))
        self.assertTrue(any("`b`" in message for message in found), found)

    def test_a_membership_change_is_reported_once_by_name(self):
        """A dump of all the unchanged contexts around the one that moved is not a finding."""
        found = differences(comparable([_status_rule("a", "b")]), comparable([_status_rule("a")]))
        self.assertEqual(len(found), 1, found)
        self.assertIn("`b`", found[0])

    def test_an_integration_id_change_is_a_difference(self):
        """The names match, so only the entry objects can carry this — and they must."""
        ours = _status_rule("a")
        theirs = _status_rule("a")
        theirs["parameters"]["required_status_checks"][0]["integration_id"] = 99
        found = differences(comparable([ours]), comparable([theirs]))
        self.assertTrue(found, "a check reported by a different app read as agreement")

    def test_a_duplicated_entry_is_a_difference(self):
        """`_contexts` is a set, so a duplicate is invisible to a name-only comparison."""
        ours = _status_rule("a")
        theirs = _status_rule("a")
        theirs["parameters"]["required_status_checks"].append(
            dict(theirs["parameters"]["required_status_checks"][0])
        )
        found = differences(comparable([ours]), comparable([theirs]))
        self.assertTrue(found, "a duplicated context entry read as agreement")

    def test_unequal_rules_always_produce_a_finding(self):
        """The invariant the two tests above are instances of: no silent disagreement.

        `differences` returning nothing for rules that do not compare equal is the fail-open this
        guard exists to prevent, one level down. Asserting the general form stops the next field
        GitHub adds inside an entry from reopening it.
        """
        cases = []
        for mutate in (
            lambda r: r["parameters"]["required_status_checks"][0].update(integration_id=99),
            lambda r: r["parameters"]["required_status_checks"][0].update(unknown_future="x"),
            lambda r: r["parameters"]["required_status_checks"].append(
                dict(r["parameters"]["required_status_checks"][0])
            ),
            lambda r: r["parameters"].update(strict_required_status_checks_policy=False),
        ):
            theirs = _status_rule("a", "b")
            mutate(theirs)
            cases.append(theirs)

        for theirs in cases:
            ours = comparable([_status_rule("a", "b")])
            enforced = comparable([theirs])
            with self.subTest(rule=theirs):
                self.assertNotEqual(ours, enforced, "test case does not differ")
                self.assertTrue(differences(ours, enforced), "unequal rules reported agreement")

    def test_a_parameter_change_still_shows_the_rule(self):
        """The dump is not gone, only reserved for what a name cannot describe."""
        found = differences(
            comparable([_status_rule("a", strict=True)]),
            comparable([_status_rule("a", strict=False)]),
        )
        self.assertEqual(len(found), 1, found)
        self.assertIn("strict_required_status_checks_policy", found[0])

    def test_a_parameter_change_is_reported(self):
        found = differences(
            comparable([_status_rule("a", strict=True)]),
            comparable([_status_rule("a", strict=False)]),
        )
        self.assertTrue(found)

    def test_a_rule_only_the_repository_has_is_reported(self):
        found = differences(
            comparable([_status_rule("a")]),
            comparable([_status_rule("a"), {"type": "required_signatures", **_SOURCE}]),
        )
        self.assertTrue(any("required_signatures" in message for message in found), found)

    def test_a_rule_only_the_manifest_has_is_reported(self):
        found = differences(
            comparable([_status_rule("a"), {"type": "required_signatures", **_SOURCE}]),
            comparable([_status_rule("a")]),
        )
        self.assertTrue(any("required_signatures" in message for message in found), found)


class FetchTest(unittest.TestCase):
    def _fetch(self, opener: _Opener):
        return fetch_rules("Syndic/unnatural_designs", "main", "token", opener, lambda _: None)

    def test_a_successful_read_is_parsed(self):
        opener = _Opener(json.dumps([_status_rule("a")]).encode())
        self.assertEqual(self._fetch(opener)[0]["type"], "required_status_checks")
        self.assertEqual(opener.calls, 1)

    def test_a_4xx_is_not_retried(self):
        """The signal case: a lost permission is discovered now, not after a backoff."""
        for code in (401, 403, 404):
            with self.subTest(code=code):
                opener = _Opener(_http_error(code))
                with self.assertRaises(UnreadableRules) as caught:
                    self._fetch(opener)
                self.assertIn(str(code), str(caught.exception))
                self.assertEqual(opener.calls, 1)

    def test_a_5xx_is_retried_and_then_fails(self):
        opener = _Opener(_http_error(500), _http_error(502), _http_error(503))
        with self.assertRaises(UnreadableRules):
            self._fetch(opener)
        self.assertEqual(opener.calls, 3)

    def test_a_blip_is_absorbed(self):
        opener = _Opener(_http_error(503), json.dumps([_status_rule("a")]).encode())
        self.assertEqual(len(self._fetch(opener)), 1)
        self.assertEqual(opener.calls, 2)

    def test_a_timeout_is_retried_and_then_fails(self):
        opener = _Opener(TimeoutError(), urllib.error.URLError("down"), TimeoutError())
        with self.assertRaises(UnreadableRules):
            self._fetch(opener)
        self.assertEqual(opener.calls, 3)

    def test_a_paginated_answer_is_unreadable(self):
        """A page is not the rule set, and comparing one against the whole manifest is a verdict
        over rules nobody looked at."""
        link = '<https://api.github.com/x?page=2>; rel="next"'
        opener = _Opener((json.dumps([_status_rule("a")]).encode(), {"Link": link}))
        with self.assertRaises(UnreadableRules) as caught:
            self._fetch(opener)
        self.assertIn("paginated", str(caught.exception))

    def test_a_last_page_link_alone_is_fine(self):
        """`rel="prev"`/`rel="last"` without a `next` means this is the end of the walk."""
        link = '<https://api.github.com/x?page=1>; rel="last"'
        opener = _Opener((json.dumps([_status_rule("a")]).encode(), {"Link": link}))
        self.assertEqual(len(self._fetch(opener)), 1)

    def test_the_request_asks_for_the_largest_page(self):
        """Left at GitHub's default of 30, the window is narrower than the union can grow."""
        self.assertIn("per_page=100", check_repository_constraints._ENDPOINT)

    def test_a_body_that_is_not_json_is_unreadable(self):
        opener = _Opener(b"<html>maintenance</html>")
        with self.assertRaises(UnreadableRules):
            self._fetch(opener)

    def test_retries_are_bounded(self):
        """A guard that retried forever would hold a required check open instead of failing."""
        opener = _Opener(*[_http_error(500)] * 10)
        with self.assertRaises(UnreadableRules):
            self._fetch(opener)
        self.assertEqual(opener.calls, 3)


class MainExitStatusTest(unittest.TestCase):
    """main() reports pass/fail, and every way of not knowing the answer is fail.

    The comparison and the transport are covered above; this covers the wiring that turns their
    results into an exit status. Untested, a guard can do all of its work correctly and still
    return 0 — which is the failure the whole manifest exists to prevent, one layer down.
    """

    def setUp(self):
        # `_problem` reads the manifest to anchor a finding at the RULES line, so main() needs a
        # root with one. A tempdir keeps the assertions off the real file's line numbers.
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        manifest = root / check_repository_constraints.MANIFEST
        manifest.parent.mkdir(parents=True)
        manifest.write_text('"""doc."""\n\nRULES = ()\n')
        self.root = root
        self.addCleanup(self.tmp.cleanup)

    def _run(self, env, fetch):
        """main() with a fake workspace and a scripted fetch, stdout captured."""
        with (
            mock.patch.object(
                check_repository_constraints, "workspace_root", return_value=self.root
            ),
            mock.patch.object(check_repository_constraints, "fetch_rules", fetch),
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return check_repository_constraints.main([]), out.getvalue()

    @staticmethod
    def _returning(rules):
        return lambda *args, **kwargs: rules

    @staticmethod
    def _raising(error):
        def fetch(*args, **kwargs):
            raise error

        return fetch

    def test_a_missing_token_fails(self):
        """No token is not "nothing to check"; it is not having checked."""
        code, out = self._run(
            {"GITHUB_REPOSITORY": "Syndic/unnatural_designs"},
            self._returning(list(RULES)),
        )
        self.assertEqual(code, 1)
        self.assertIn("GITHUB_TOKEN", out)

    def test_a_missing_repository_fails(self):
        code, out = self._run({"GITHUB_TOKEN": "t"}, self._returning(list(RULES)))
        self.assertEqual(code, 1)
        self.assertIn("GITHUB_REPOSITORY", out)

    def test_an_unreadable_answer_fails(self):
        """The transport raising must reach the exit status rather than being swallowed."""
        code, out = self._run(
            {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "Syndic/unnatural_designs"},
            self._raising(UnreadableRules("403 Forbidden")),
        )
        self.assertEqual(code, 1)
        self.assertIn("403 Forbidden", out)

    def test_agreement_passes(self):
        code, out = self._run(
            {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "Syndic/unnatural_designs"},
            self._returning(list(RULES)),
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_a_dropped_required_check_fails(self):
        """The case the guard exists for, driven all the way through main()."""
        rules = json.loads(json.dumps(list(RULES)))
        for rule in rules:
            if rule["type"] == "required_status_checks":
                dropped = rule["parameters"]["required_status_checks"].pop()["context"]
                break
        code, out = self._run(
            {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "Syndic/unnatural_designs"},
            self._returning(rules),
        )
        self.assertEqual(code, 1)
        self.assertIn(dropped, out)

    def test_a_finding_is_anchored_at_the_manifest(self):
        """The `file:line:start-end:` shape .vscode/tasks.json's problem matcher parses."""
        code, out = self._run(
            {"GITHUB_REPOSITORY": "Syndic/unnatural_designs"},
            self._returning(list(RULES)),
        )
        self.assertEqual(code, 1)
        self.assertTrue(
            out.startswith(f"{check_repository_constraints.MANIFEST}:3:1-6: "),
            out,
        )


if __name__ == "__main__":
    unittest.main()
