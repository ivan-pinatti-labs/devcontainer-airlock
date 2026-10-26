"""Tests for scripts/extension-index.py.

The Marketplace is replaced by canned answers to its extension query, and
the pin file by a copy in a scratch directory.
"""

from __future__ import annotations

import io
import json

import pytest
from conftest import load

SCRIPT = "scripts/extension-index.py"


def release(version, date, platform=None, pre=False):
    v = {"version": version, "lastUpdated": f"{date}T12:00:00.5Z"}
    if platform:
        v["targetPlatform"] = platform
    if pre:
        v["properties"] = [
            {"key": "Microsoft.VisualStudio.Code.PreRelease", "value": "true"}
        ]
    else:
        v["properties"] = [{"key": "Other", "value": "true"}]
    return v


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def ext(tmp_path, monkeypatch):
    module = load(SCRIPT, "extension_index")
    path = tmp_path / "extensions.txt"
    monkeypatch.setattr(module, "PINS", path)
    module.marketplace = {}

    def urlopen(req, timeout):
        ext_id = json.loads(req.data)["filters"][0]["criteria"][0]["value"]
        if ext_id not in module.marketplace:
            return FakeResponse(json.dumps({"results": [{"extensions": []}]}).encode())
        versions = module.marketplace[ext_id]
        if isinstance(versions, Exception):
            raise versions
        return FakeResponse(
            json.dumps({"results": [{"extensions": [{"versions": versions}]}]}).encode()
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    return module


def test_read_pins_keeps_the_identifier_as_written(ext):
    ext.PINS.write_text("# pinned\n\nPub.Ext@1.0.0\nother.thing@2\n")
    assert ext.read_pins() == ["Pub.Ext", "other.thing"]


def test_read_pins_refuses_a_line_it_cannot_read(ext, capsys):
    ext.PINS.write_text("not a pin\n")
    with pytest.raises(SystemExit) as e:
        ext.read_pins()
    assert e.value.code == 2
    assert "cannot read 'not a pin'" in capsys.readouterr().err


def test_releases_refuses_an_extension_the_marketplace_lacks(ext, capsys):
    with pytest.raises(SystemExit) as e:
        ext.releases("pub.gone")
    assert e.value.code == 2
    assert "pub.gone is not on the Marketplace" in capsys.readouterr().err


def test_index_lists_stable_linux_releases_newest_first(ext):
    ext.marketplace["pub.ext"] = [
        release("5.0.0", "2026-10-20", pre=True),
        release("4.0.0", "2026-10-15", platform="darwin-arm64"),
        release("3.0.0", "2026-10-10", platform="linux-x64"),
        release("2.0.0", "2026-10-01"),
        release("1.0.0", "2026-09-01"),
    ]
    data = ext.index("pub.ext")
    assert [r["version"] for r in data["releases"]] == ["3.0.0", "2.0.0", "1.0.0"]
    assert data["releases"][0]["releaseTimestamp"] == "2026-10-10T12:00:00.500000+00:00"
    assert data["homepage"].endswith("/items?itemName=pub.ext")
    assert data["changelogUrl"].endswith("/items/pub.ext/changelog")


def test_index_dates_a_repeated_version_by_its_latest_upload(ext):
    ext.marketplace["pub.ext"] = [
        release("2.0.0", "2026-10-01"),
        release("2.0.0", "2026-10-05", platform="linux-x64"),
        release("2.0.0", "2026-10-03"),
    ]
    data = ext.index("pub.ext")
    assert data["releases"] == [
        {"version": "2.0.0", "releaseTimestamp": "2026-10-05T12:00:00.500000+00:00"}
    ]


@pytest.mark.parametrize("argv", [["--check"], ["out"]])
def test_main_refuses_any_argument(ext, capsys, argv):
    assert ext.main(argv) == 2
    err = capsys.readouterr().err
    assert "> index.json" in err and "Runs anywhere" not in err


def test_main_prints_one_entry_per_extension(ext, capsys):
    ext.PINS.write_text("# keep\nPub.Ext@1.0.0\npub.other@3.0.0\n")
    ext.marketplace["Pub.Ext"] = [release("2.0.0", "2026-10-01")]
    ext.marketplace["pub.other"] = [release("3.0.0", "2026-09-01")]
    assert ext.main([]) == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert list(data) == ["Pub.Ext", "pub.other"]
    assert data["Pub.Ext"]["releases"][0]["version"] == "2.0.0"
    assert "Pub.Ext: 1 stable release(s)" in err


def test_main_says_when_the_marketplace_cannot_be_read(ext, capsys):
    ext.PINS.write_text("pub.ext@1.0.0\n")
    ext.marketplace["pub.ext"] = OSError("unreachable")
    assert ext.main([]) == 2
    out, err = capsys.readouterr()
    assert "could not be read: unreachable" in err
    assert out == ""
