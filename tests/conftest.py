"""Shared test plumbing.

The default suite never touches the network (BUILD-SPEC 11.4). Recorded responses live
in ``tests/fixtures`` and are served by matching a substring of the request URL, so a
test declares only the sources it depends on and an unexpected fetch fails loudly
instead of quietly reaching the internet.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never read or write the real ~/.company-research during tests."""
    home = tmp_path / "cr-home"
    monkeypatch.setattr(common, "HOME", home)
    monkeypatch.setattr(common, "DEFAULT_CACHE_DIR", home / "cache")
    monkeypatch.setattr(common, "PROFILE_PATH", home / "profile.yaml")
    monkeypatch.setattr(common, "SNAPSHOT_DIR", home / "snapshots")
    monkeypatch.setattr(common, "DOSSIER_DIR", home / "dossiers")
    monkeypatch.setattr(common, "WATCHLIST_PATH", home / "watchlist.txt")
    monkeypatch.setenv("CR_CONTACT_EMAIL", "tests@example.com")
    monkeypatch.setenv("CR_OFFLINE", "1")
    return home


def load_fixture(name: str) -> str:
    path = FIXTURES / name
    if not path.exists():
        raise AssertionError(f"missing fixture {name}; record it into {FIXTURES}")
    return path.read_text(encoding="utf-8")


@pytest.fixture
def fake_http(monkeypatch):
    """Serve recorded responses by URL substring.

    Usage::

        fake_http({"wbsearchentities": "wikidata_search_infosys.json"})
    """

    def install(routes: dict[str, str], strict: bool = True):
        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            for needle, fixture in routes.items():
                if needle in url:
                    body = load_fixture(fixture)
                    return common.Response(
                        url=url, status=200, headers={}, text=body,
                        retrieved_at="2026-08-27T00:00:00Z", from_cache=True,
                    )
            if strict:
                raise common.SourceError(f"no fixture registered for {url}")
            raise common.SourceError(f"unavailable in tests: {url}")

        monkeypatch.setattr(common, "http_get", fake_get)
        return calls

    return install


class Dossier:
    """A dossier directory plus a shorthand for dropping fragments into it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        (path / "evidence").mkdir(parents=True, exist_ok=True)

    def write(self, name: str, payload: dict) -> Path:
        target = self.path / "evidence" / f"{name}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return target


@pytest.fixture
def dossier(tmp_path) -> Dossier:
    return Dossier(tmp_path / "acme.com-2026-08-27")


@pytest.fixture(scope="session")
def node_available() -> bool:
    import shutil

    return shutil.which("node") is not None
