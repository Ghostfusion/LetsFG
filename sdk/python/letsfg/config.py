"""The LetsFG local config store: one file, one location, resolved in one place.

`config.json` holds both credential kinds:

- `pfs_auth` — the card-backed PFS token written by ``letsfg auth``
- `api_key` / ``agent_id`` — the Developer API key

The location follows the same rule on every platform, so the CLI, the SDKs and
the QML plugin cannot drift apart:

- Windows: ``%APPDATA%\\.letsfg\\config.json``
- otherwise: ``~/.letsfg/config.json``

Earlier versions kept the Developer API key in a *different* directory
(``%APPDATA%\\letsfg`` on Windows, ``$XDG_CONFIG_HOME/letsfg`` on POSIX), which
is why ``load_config`` also reads those paths and ``save_config`` folds them into
the canonical file. Reads fall back; only the canonical path is ever written.

Writes are atomic and owner-only. The file carries a rotating refresh token, so a
torn write costs the user a new card connect.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CONFIG_DIR = ".letsfg"
CONFIG_FILE = "config.json"


def _appdata() -> Path:
    """Windows configuration base (%APPDATA%, falling back to the home dir)."""
    return Path(os.environ.get("APPDATA", Path.home()))


def config_dir() -> Path:
    """The directory LetsFG keeps its config in, on this platform."""
    return (_appdata() if os.name == "nt" else Path.home()) / CONFIG_DIR


def config_path() -> Path:
    """The config file: both the PFS token and the Developer API key live here."""
    return config_dir() / CONFIG_FILE


def legacy_config_paths() -> tuple[Path, ...]:
    """Where the Developer API key used to live, before the stores were unified.

    Read-only fallbacks, so moving to one directory does not orphan a key a user
    already saved. Nothing is ever written here again.
    """
    if os.name == "nt":
        return (_appdata() / "letsfg" / CONFIG_FILE,)
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (xdg / "letsfg" / CONFIG_FILE,)


def load_config() -> dict:
    """Read the store, falling back to a legacy location. Never raises.

    A corrupt or unreadable file reads as an empty store: the callers turn that
    into "not authenticated yet", which is a message the user can act on.
    """
    for path in (config_path(), *legacy_config_paths()):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def save_config(data: dict) -> None:
    """Merge `data` into the store and write it atomically, owner-only.

    The merge is what lets the token writer and the API-key writer share a file
    without either dropping the other's keys.
    """
    current = load_config()
    current.update(data)
    atomic_write_json(config_path(), current)


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
