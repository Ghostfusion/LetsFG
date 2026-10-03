"""The two local config stores: pinned locations, atomic owner-only writes.

The bug these cover: the token store and the credentials store each resolved
their own path, and both wrote with "write, then chmod" — so a write interrupted
halfway (or a chmod that had not landed yet) could leave the rotating refresh
token unreadable or world-readable.

No test here may depend on the developer's environment having a variable set
(working agreement rule 1): platform bases are injected through monkeypatch.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from letsfg.config import atomic_write_json, credentials_store_path, token_store_path


def test_the_two_stores_are_different_files():
    """They hold different credentials; one resolver must not conflate them."""
    token = token_store_path()
    credentials = credentials_store_path()

    assert token.name == "config.json" and credentials.name == "config.json"
    assert token.parent.name == ".letsfg"
    assert credentials.parent.name == "letsfg"
    assert token != credentials
    assert token.is_absolute() and credentials.is_absolute()


def test_token_store_ignores_xdg(monkeypatch, tmp_path):
    """The token path is the documented one and is shared with the JS SDK."""
    if os.name == "nt":
        pytest.skip("posix path rule")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert token_store_path() == Path.home() / ".letsfg" / "config.json"


def test_credentials_store_honours_xdg(monkeypatch, tmp_path):
    if os.name == "nt":
        pytest.skip("posix path rule")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert credentials_store_path() == tmp_path / "letsfg" / "config.json"


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
    """A failed write must not leave a half-written token store behind."""
    target = tmp_path / "config.json"
    atomic_write_json(target, {"ok": True})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]
