"""
Every error the SDK raises carries a machine-readable code and category.

`AGENTS.md` tells agents they can branch on `error_code` / `error_category`, and
the JS SDK set the bar by hard-coding `AUTH_INVALID` on its
`AuthenticationError`. Python left the field empty on the paths that never saw
the shared HTTP seam — `_require_api_key` and `register` — and its free-lane
`BearerTokenError` had no such fields at all, so a caller branching on the
documented field got `""` or an AttributeError.
"""
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SDK_PYTHON_ROOT = PROJECT_ROOT / "sdk" / "python"
if str(SDK_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_PYTHON_ROOT))

from letsfg import client as C
from letsfg.connectors import auth as A
from letsfg.connectors.auth import BearerTokenError


def _http_error(status: int, body: bytes) -> HTTPError:
    return HTTPError("https://letsfg.co", status, "err", {}, io.BytesIO(body))


class AuthenticationErrorCodeTest(unittest.TestCase):
    def _missing_key_error(self) -> C.AuthenticationError:
        # No bearer token (the free lane gives up) and no API key (neither the
        # argument, the env var, nor the saved config), so the paid lane refuses.
        # `search` imports get_bearer_token inside the method, so the patch has
        # to land on the source module.
        with patch.object(A, "get_bearer_token", side_effect=BearerTokenError("no token")), \
             patch.object(C, "_saved_api_key", lambda: ""), \
             patch.dict(os.environ, {"LETSFG_API_KEY": ""}):
            client = C.LetsFG()
            with self.assertRaises(C.AuthenticationError) as cm:
                client.search("GDN", "BCN", "2027-06-15")
        return cm.exception

    def test_a_missing_key_carries_auth_invalid(self):
        err = self._missing_key_error()
        self.assertEqual(err.error_code, C.ErrorCode.AUTH_INVALID)
        self.assertEqual(err.error_category, C.ErrorCategory.BUSINESS)
        self.assertFalse(err.is_retryable, "retrying does not conjure an API key")
        self.assertIn("letsfg", str(err))

    def test_the_http_seam_keeps_the_servers_own_code(self):
        # The class default must not overwrite a code the server actually sent.
        err = C.AuthenticationError("Unauthorized", status_code=401, error_code="TOKEN_REVOKED")
        self.assertEqual(err.error_code, "TOKEN_REVOKED")


class BearerTokenErrorFieldsTest(unittest.TestCase):
    def test_it_carries_the_documented_fields(self):
        err = BearerTokenError("no token")
        self.assertEqual(err.error_code, "AUTH_INVALID")
        self.assertEqual(err.error_category, "business")
        self.assertFalse(err.is_retryable)


class RegistrationErrorCodeTest(unittest.TestCase):
    def test_a_registration_failure_carries_an_inferred_code(self):
        def raise_it(req, timeout=None):
            raise _http_error(429, b'{"detail":"slow down"}')

        with patch.object(C, "urlopen", raise_it):
            with self.assertRaises(C.LetsFGError) as cm:
                C.LetsFG.register("agent", "agent@example.com")

        self.assertEqual(cm.exception.error_code, C.ErrorCode.RATE_LIMITED)
        self.assertEqual(cm.exception.status_code, 429)
        self.assertTrue(cm.exception.is_retryable, "429 is transient")

    def test_the_servers_code_wins_over_the_inferred_one(self):
        def raise_it(req, timeout=None):
            raise _http_error(500, b'{"detail":"boom","error_code":"SERVICE_UNAVAILABLE"}')

        with patch.object(C, "urlopen", raise_it):
            with self.assertRaises(C.LetsFGError) as cm:
                C.LetsFG.register("agent", "agent@example.com")

        self.assertEqual(cm.exception.error_code, C.ErrorCode.SERVICE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
