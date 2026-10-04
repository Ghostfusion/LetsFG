"""
Every error the SDK raises carries a machine-readable code and category.

`AGENTS.md` tells agents they can branch on `error_code` / `error_category`, and
the JS SDK set the bar by hard-coding `AUTH_INVALID` on its
`AuthenticationError`. Python left the field empty on the paths that never saw an
HTTP response — `_require_api_key` being the one an agent hits first — so a
caller branching on `error_code` got `""`.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SDK_PYTHON_ROOT = PROJECT_ROOT / "sdk" / "python"
if str(SDK_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_PYTHON_ROOT))

from letsfg import client as C
from letsfg.connectors import auth as A
from letsfg.connectors.auth import BearerTokenError


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


if __name__ == "__main__":
    unittest.main()
