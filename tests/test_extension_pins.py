"""Tests for scripts/extension-pins.py.

The Marketplace is replaced by canned answers to its extension query, and
the pin file by a copy in a scratch directory.
"""

from __future__ import annotations

import datetime
import io
import json

import pytest
from conftest import load

SCRIPT = "scripts/extension-pins.py"
NOW = datetime.datetime.now(datetime.UTC)


def days_ago(n):
    return (NOW - datetime.timedelta(days=n)).isoformat().replace("+00:00", "Z")


def release(version, age, platform=None, pre=False):
    v = {"version": version, "lastUpdated": days_ago(age)}
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
def pins(tmp_path, monkeypatch):
    module = load(SCRIPT, "extension_pins")
    path = tmp_path / "a/b/c/extensions.txt"
    path.parent.mkdir(parents=True)
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


def test_read_pins_skips_comments_and_blank_lines(pins):
    pins.PINS.write_text("# pinned\n\nPub.Ext@1.0.0\nother.thing@2\n")
    assert pins.read_pins() == {"pub.ext": "1.0.0", "other.thing": "2"}


def test_read_pins_refuses_a_line_it_cannot_read(pins, capsys):
    pins.PINS.write_text("not a pin\n")
    with pytest.raises(SystemExit) as e:
        pins.read_pins()
    assert e.value.code == 2
    assert "cannot read 'not a pin'" in capsys.readouterr().err


def test_releases_refuses_an_extension_the_marketplace_lacks(pins, capsys):
    with pytest.raises(SystemExit) as e:
        pins.releases("pub.gone")
    assert e.value.code == 2
    assert "pub.gone is not on the Marketplace" in capsys.readouterr().err


def test_eligible_takes_the_newest_stable_old_enough_release(pins):
    pins.marketplace["pub.ext"] = [
        release("5.0.0", 1),
        release("4.0.0", 30, pre=True),
        release("3.0.0", 30, platform="darwin-arm64"),
        release("2.1.0", 20, platform="linux-x64"),
        release("2.0.0", 40),
    ]
    assert pins.eligible("pub.ext", "1.0.0", NOW) == ("2.1.0", days_ago(20)[:10])


def test_eligible_never_moves_past_the_pin(pins):
    pins.marketplace["pub.ext"] = [release("2.0.0", 1), release("1.0.0", 40)]
    assert pins.eligible("pub.ext", "1.0.0", NOW) == (None, None)


def test_eligible_with_nothing_old_enough(pins):
    pins.marketplace["pub.ext"] = [release("2.0.0", 1)]
    assert pins.eligible("pub.ext", "9.9.9", NOW) == (None, None)


def test_main_refuses_unknown_arguments(pins, capsys):
    assert pins.main([]) == 2
    err = capsys.readouterr().err
    assert "--check" in err and "Runs anywhere" not in err


def test_main_check_reports_due_updates(pins, capsys):
    pins.PINS.write_text("pub.ext@1.0.0\npub.current@3.0.0\n")
    pins.marketplace["pub.ext"] = [release("2.0.0", 10)]
    pins.marketplace["pub.current"] = [release("3.0.0", 10)]
    assert pins.main(["--check"]) == 1
    assert (
        f"pub.ext: 1.0.0 -> 2.0.0 (released {days_ago(10)[:10]})"
        in capsys.readouterr().out
    )


def test_main_check_with_every_pin_current(pins, capsys):
    pins.PINS.write_text("pub.ext@1.0.0\n")
    pins.marketplace["pub.ext"] = [release("1.0.0", 10)]
    assert pins.main(["--check"]) == 0
    assert "every pin is current" in capsys.readouterr().out


def test_main_update_rewrites_the_pins(pins, capsys):
    pins.PINS.write_text("# keep\nPub.Ext@1.0.0\npub.current@3.0.0\n")
    pins.marketplace["pub.ext"] = [release("2.0.0", 10)]
    pins.marketplace["pub.current"] = [release("3.0.0", 10)]
    assert pins.main(["--update"]) == 0
    assert pins.PINS.read_text() == "# keep\nPub.Ext@2.0.0\npub.current@3.0.0\n"
    assert "rewrote 1 pin(s) in a/b/c/extensions.txt" in capsys.readouterr().out


def test_main_says_when_the_marketplace_cannot_be_read(pins, capsys):
    pins.PINS.write_text("pub.ext@1.0.0\n")
    pins.marketplace["pub.ext"] = OSError("unreachable")
    assert pins.main(["--check"]) == 2
    assert "could not be read: unreachable" in capsys.readouterr().err
