"""The local config store: one file, atomic owner-only writes, legacy reads.

The bugs these cover:

- `client.py` and `connectors/auth.py` each resolved their own config
  directory, so the Developer API key and the PFS token could land in different
  files (and on Windows, in directories differing only by a leading dot);
- both wrote with "write, then chmod", so an interrupted write could lose the
  rotating refresh token, and the chmod window exposed it.

No test here may depend on the developer's environment having a variable set
(working agreement rule 1): the platform base is patched per test.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from letsfg.config import (
    atomic_write_json,
    config_dir,
    config_path,
    legacy_config_paths,
    load_config,
    save_config,
)


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Point the store, and any legacy fallback, at a temporary directory."""
    if os.name == "nt":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    else:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "legacy"))
    return tmp_path


def test_there_is_one_config_file_per_platform(store):
    assert config_dir().name == ".letsfg"
    assert config_path() == config_dir() / "config.json"
    assert config_dir().parent == store, "the config lives directly under the platform base"
    assert config_path() not in legacy_config_paths(), "the canonical path is never also a fallback"


def test_a_legacy_key_is_read_and_then_migrated(store):
    legacy = legacy_config_paths()[0]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"api_key": "legacy-key"}), encoding="utf-8")

    # Reads fall back, so an existing key is not orphaned by the move.
    assert load_config()["api_key"] == "legacy-key"

    save_config({"agent_id": "ag_1"})

    migrated = json.loads(config_path().read_text(encoding="utf-8"))
    assert migrated == {"api_key": "legacy-key", "agent_id": "ag_1"}
    assert load_config() == migrated


def test_the_canonical_file_wins_over_the_legacy_one(store):
    legacy = legacy_config_paths()[0]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"api_key": "old"}), encoding="utf-8")

    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"api_key": "current"}), encoding="utf-8")

    assert load_config()["api_key"] == "current"


def test_the_two_writers_share_the_file_without_dropping_each_other(store):
    """`letsfg auth` writes the token; the Developer lane writes the API key."""
    save_config({"api_key": "letsfg_key"})
    save_config({"pfs_auth": {"token": "tok", "refresh_token": "ref"}})

    stored = json.loads(config_path().read_text(encoding="utf-8"))
    assert stored["api_key"] == "letsfg_key"
    assert stored["pfs_auth"] == {"token": "tok", "refresh_token": "ref"}


def test_atomic_write_creates_the_file_owner_only(tmp_path):
    target = tmp_path / "nested" / "config.json"
    atomic_write_json(target, {"pfs_auth": {"token": "example"}})

    assert json.loads(target.read_text(encoding="utf-8")) == {"pfs_auth": {"token": "example"}}
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["config.json"], "a temp file was left behind"


def test_atomic_write_replaces_the_previous_file(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_json(target, {"first": True})
    atomic_write_json(target, {"second": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"second": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


def test_failed_write_keeps_the_previous_file(tmp_path):
    """A failed write must not leave a half-written store behind."""
    target = tmp_path / "config.json"
    atomic_write_json(target, {"ok": True})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]
