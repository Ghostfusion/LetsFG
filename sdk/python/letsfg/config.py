"""Where the LetsFG SDK keeps its two local stores, and how they are written.

There are two credentials, kept in two files on purpose, and both locations are
resolved here — in one place — so the CLI, the client and the auth flow cannot
drift apart:

- **token store** — ``~/.letsfg/config.json`` (``%APPDATA%\\.letsfg\\config.json``
  on Windows): the card-backed PFS token written by ``letsfg auth`` under
  ``pfs_auth``. This is the path the documentation names, and the JS SDK uses
  the same one.
- **credentials store** — ``~/.config/letsfg/config.json``
  (``%APPDATA%\\letsfg\\config.json`` on Windows; ``XDG_CONFIG_HOME`` is
  honoured): the Developer API key (``api_key``, ``agent_id``).

Writes are atomic and owner-only. The config carries a rotating refresh token, so
a torn write costs the user a new card connect — which is what the previous
write-then-chmod implementation risked on every token refresh.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Directory names under the platform config base. Kept as literals so the two
# stores stay greppable: the token store's leading dot is part of the path the
# docs and the JS SDK use.
TOKEN_DIR = ".letsfg"
CREDENTIALS_DIR = "letsfg"


def _appdata() -> Path:
    """Windows configuration base (%APPDATA%, falling back to the home dir)."""
    return Path(os.environ.get("APPDATA", Path.home()))


def token_store_path() -> Path:
    """The PFS token store written by ``letsfg auth`` (documented path)."""
    base = _appdata() if os.name == "nt" else Path.home()
    return base / TOKEN_DIR / "config.json"


def credentials_store_path() -> Path:
    """The Developer API key store, alongside other application config."""
    if os.name == "nt":
        base = _appdata()
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / CREDENTIALS_DIR / "config.json"


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON owner-only and atomically: create at 0600, then rename over it.

    A temporary file in the same directory is renamed onto the target, so a
    reader either sees the previous complete file or the new complete one. The
    file is created with owner-only permissions rather than chmod-ed afterwards,
    which left a window where the rotating refresh token was world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
