"""Smoke test for the pyproject -> uv.lock -> requirements_lock -> pip.parse chain.

Deleted once the chain is verified end-to-end; see the BUILD file for context.
"""

import unittest


class PyPiHubReachableTest(unittest.TestCase):
    def test_third_party_import_resolves(self) -> None:
        import requests  # noqa: PLC0415  - import inside test is the point

        self.assertTrue(hasattr(requests, "get"))


if __name__ == "__main__":
    unittest.main()
