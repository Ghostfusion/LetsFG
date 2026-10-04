"""
An exhausted poll is a timeout, not an empty result set.

`search_local` used to return `{"offers": [], "total_results": 0}` when the
search never reached a terminal status, so `letsfg search` printed
"No flights found for GDN → BCN on 2026-06-15" — a claim about the market the
server never made, and one no caller could tell apart from a genuine empty.

Canonical rule: `docs/trvl-study-design.md` §2.1 — an empty result may only be
reported as no-results when coverage is complete. The JS SDK already throws on
this path (`sdk/js/src/index.ts`, `searchPFS`).
"""
import asyncio
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SDK_PYTHON_ROOT = PROJECT_ROOT / "sdk" / "python"
if str(SDK_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_PYTHON_ROOT))

from letsfg import local as L
from letsfg.client import ErrorCode, LetsFGError


def _body(payload: dict) -> io.BytesIO:
    """What `with urlopen(...) as resp:` is handed back here."""
    return io.BytesIO(json.dumps(payload).encode())


class ExhaustedPollTest(unittest.TestCase):
    def _search(self):
        def fake_urlopen(req, timeout=None):
            # POST /api/search hands out an id; every poll stays in-progress.
            if req.get_method() == "POST":
                return _body({"search_id": "ws_x"})
            return _body({"status": "searching"})

        async def no_sleep(_seconds):
            return None

        with patch.object(L, "ensure_bearer_token", lambda: "lfg_at_x"), \
             patch.object(L, "urlopen", fake_urlopen), \
             patch.object(L.asyncio, "sleep", no_sleep):
            return asyncio.run(L.search_local("GDN", "BCN", "2026-06-15"))

    def test_an_exhausted_poll_raises_instead_of_returning_an_empty_result(self):
        with self.assertRaises(LetsFGError) as cm:
            self._search()

        err = cm.exception
        self.assertEqual(err.status_code, 504, "a give-up is a timeout, not a result")
        self.assertEqual(err.error_code, ErrorCode.SUPPLIER_TIMEOUT)
        self.assertTrue(err.is_retryable, "a timeout is transient — retrying is safe")

    def test_the_timeout_carries_the_search_id_so_the_caller_can_poll_on(self):
        with self.assertRaises(LetsFGError) as cm:
            self._search()

        self.assertIn("ws_x", str(cm.exception))
        self.assertIn("/api/results/ws_x", str(cm.exception))

    def test_a_real_empty_result_still_returns_an_empty_list(self):
        # The other half of the rule: when the engine DOES answer with nothing,
        # that is a result and must keep being returned as one.
        def fake_urlopen(req, timeout=None):
            if req.get_method() == "POST":
                return _body({"search_id": "ws_y"})
            return _body({"status": "completed", "offers": [], "total_results": 0})

        async def no_sleep(_seconds):
            return None

        with patch.object(L, "ensure_bearer_token", lambda: "lfg_at_x"), \
             patch.object(L, "urlopen", fake_urlopen), \
             patch.object(L.asyncio, "sleep", no_sleep):
            result = asyncio.run(L.search_local("GDN", "BCN", "2026-06-15"))

        self.assertEqual(result["offers"], [])
        self.assertEqual(result["total_results"], 0)


if __name__ == "__main__":
    unittest.main()
