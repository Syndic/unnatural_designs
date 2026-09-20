"""Holds the one guard that reads outside the tree to its fail-closed promise.

Two halves, and the second is the one worth having. The comparison is a pure function and is
tested the ordinary way. The *retry policy* is where "fail closed" is either true or merely
claimed: a guard that swallowed a 403, or that treated an unreadable body as agreement, would go
green having checked nothing — the exact shape the manifest exists to stop one level up. So the
transport is exercised with an injected opener rather than described in a comment.
"""

from __future__ import annotations

import email.message
import json
import unittest
import urllib.error

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
    def __init__(self, body: bytes):
        self._body = body

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


if __name__ == "__main__":
    unittest.main()
