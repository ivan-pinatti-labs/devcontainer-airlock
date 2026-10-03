"""Tests for the mirror gate's filters and OSV feed (images/mirror-gate/lib).

Every case of images/mirror-gate/test_gate.py, which the image build runs,
is here too, plus the rest of each module. The OSV downloads are served from
memory: urllib.request.urlopen is replaced, and the cache is a scratch
directory.
"""

# cspell:words fooo pytz
from __future__ import annotations

import datetime
import io
import json
import sys
import urllib.error
import zipfile

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "images/mirror-gate/lib"))

import filters
import osvdb

NOW = datetime.datetime(2026, 9, 29, tzinfo=datetime.timezone.utc)
WHOLE = {"ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}]}]}


def entry(ecosystem, name, **extra):
    return json.dumps(
        {"affected": [{"package": {"ecosystem": ecosystem, "name": name}, **extra}]}
    )


def feed(ecosystem, *entries):
    f = osvdb.Feed(ecosystem)
    for i, data in enumerate(entries):
        f._add(f"MAL-{i}", data)
    f._index()
    return f


@pytest.fixture
def npm():
    return feed(
        "npm",
        entry("npm", "evil-pkg", **WHOLE),
        entry("npm", "left-pad", versions=["1.3.1"]),
        json.dumps(
            {
                "withdrawn": "2026-01-01",
                "affected": [
                    {
                        "package": {"ecosystem": "npm", "name": "was-bad"},
                        "ranges": [{"events": [{"introduced": "0"}]}],
                    }
                ],
            }
        ),
    )


@pytest.fixture
def pypi():
    return feed("PyPI", entry("PyPI", "Evil_Pkg", versions=["1.0"]))


@pytest.fixture
def go():
    return feed("Go", entry("Go", "github.com/Evil/mod", versions=["v1.2.3"]))


# The cases the image build runs ---------------------------------------------


def test_feed_blocks_what_the_entries_name(npm, pypi):
    assert npm.blocked("evil-pkg", "9.9.9") is True
    assert npm.blocked("left-pad", "1.3.1") is True
    assert npm.blocked("left-pad", "1.3.0") is False
    assert npm.blocked("was-bad", "1.0.0") is False
    assert pypi.blocked("evil-pkg", "1.0") is True
    assert pypi.blocked("evil_pkg", "1.1") is False


DOC = {
    "name": "left-pad",
    "dist-tags": {"latest": "1.3.1", "beta": "1.3.1"},
    "versions": {"1.3.0": {}, "1.3.1": {}, "1.4.0": {}},
    "time": {
        "1.3.0": "2020-01-01T00:00:00Z",
        "1.3.1": "2021-01-01T00:00:00Z",
        "1.4.0": "2026-09-27T00:00:00Z",
    },
}


def test_npm_packument_removes_malicious_and_moves_latest(npm):
    body, removed = filters.npm_packument(json.dumps(DOC).encode(), npm, 0, NOW)
    out = json.loads(body)
    assert sorted(removed) == ["1.3.1"]
    assert out["dist-tags"]["latest"] == "1.4.0"
    assert "beta" not in out["dist-tags"]


def test_npm_packument_min_age_removes_young(npm):
    body, removed = filters.npm_packument(json.dumps(DOC).encode(), npm, 7, NOW)
    assert sorted(removed) == ["1.3.1", "1.4.0"]
    assert json.loads(body)["dist-tags"]["latest"] == "1.3.0"


def test_npm_paths():
    assert filters.npm_tarball("left-pad/-/left-pad-1.3.1.tgz") == ("left-pad", "1.3.1")
    assert filters.npm_tarball("@scope/pkg/-/pkg-2.0.0-rc.1.tgz") == (
        "@scope/pkg",
        "2.0.0-rc.1",
    )
    assert filters.npm_tarball("@scope%2fpkg/-/pkg-2.0.0.tgz") == (
        "@scope/pkg",
        "2.0.0",
    )
    assert filters.npm_tarball("left-pad") is None
    assert filters.npm_package("@scope%2fpkg") == "@scope/pkg"


HTML = (
    b'<html><body>\n<a href="../../packages/a/evil_pkg-1.0-py3-none-any.whl#sha256=1">'
    b'evil_pkg-1.0-py3-none-any.whl</a><br/>\n<a href="../../packages/b/evil_pkg-1.1.tar.gz#sha256=2">'
    b"evil_pkg-1.1.tar.gz</a><br/>\n</body></html>"
)


def test_pypi_files_and_index(pypi):
    assert filters.pypi_file("Evil_Pkg-1.0-py3-none-any.whl") == ("Evil_Pkg", "1.0")
    assert filters.pypi_file("evil-pkg-1.0.tar.gz") == ("evil-pkg", "1.0")
    body, removed = filters.pypi_simple(HTML, pypi)
    assert removed == ["evil_pkg-1.0-py3-none-any.whl"]
    assert b"evil_pkg-1.1.tar.gz" in body
    assert b"1.0-py3" not in body


def test_go_requests_and_list(go):
    assert filters.go_decode("github.com/!evil/mod") == "github.com/Evil/mod"
    assert filters.go_request("github.com/!evil/mod/@v/list") == (
        "github.com/Evil/mod",
        "list",
        None,
    )
    assert filters.go_request("github.com/!evil/mod/@v/v1.2.3.zip") == (
        "github.com/Evil/mod",
        "zip",
        "v1.2.3",
    )
    assert filters.go_request("github.com/!evil/mod/@latest") == (
        "github.com/Evil/mod",
        "latest",
        None,
    )
    body, removed = filters.go_list(
        b"v1.2.2\nv1.2.3\nv1.3.0\n", "github.com/Evil/mod", go
    )
    assert (body, removed) == (b"v1.2.2\nv1.3.0\n", ["v1.2.3"])


def test_too_young():
    assert filters.too_young("2026-09-28T00:00:00Z", 7, NOW) is True
    assert filters.too_young("2026-09-01T00:00:00Z", 7, NOW) is False
    assert filters.too_young("2026-09-28T00:00:00Z", 0, NOW) is False


# The rest of filters ---------------------------------------------------------


def test_parse_time_refuses_what_is_not_a_time():
    assert filters.parse_time(None) is None
    assert filters.parse_time("yesterday") is None


def test_too_young_takes_a_datetime_and_a_missing_time():
    assert filters.too_young(NOW, 7, NOW) is True
    assert filters.too_young(None, 7, NOW) is False


def test_npm_packument_untouched_when_nothing_is_removed(npm):
    body = json.dumps({"name": "fine", "versions": {"1.0.0": {}}}).encode()
    assert filters.npm_packument(body, npm, 0, NOW) == (body, [])


def test_npm_packument_drops_latest_when_nothing_is_left(npm):
    doc = {
        "name": "evil-pkg",
        "dist-tags": {"latest": "1.0.0"},
        "versions": {"1.0.0": {}},
    }
    body, removed = filters.npm_packument(json.dumps(doc).encode(), npm, 0, NOW)
    assert removed == ["1.0.0"]
    assert json.loads(body)["dist-tags"] == {}


def test_npm_packument_leaves_tags_on_kept_versions(npm):
    doc = {
        "name": "left-pad",
        "dist-tags": {"latest": "1.3.0", "next": "1.3.1"},
        "versions": {"1.3.0": {}, "1.3.1": {}},
    }
    body, _ = filters.npm_packument(json.dumps(doc).encode(), npm, 0, NOW)
    assert json.loads(body)["dist-tags"] == {"latest": "1.3.0"}


def test_npm_tarball_of_another_package_is_not_one():
    assert filters.npm_tarball("left-pad/-/right-pad-1.0.0.tgz") is None


def test_npm_package_refuses_a_deeper_path():
    assert filters.npm_package("left-pad/1.0.0/extra") is None


def test_pypi_file_names_it_cannot_read():
    assert filters.pypi_file("short-1.0.whl") is None
    assert filters.pypi_file("noversion.tar.gz") is None
    assert filters.pypi_file("evil-pkg-1.0.exe") is None


def test_pypi_simple_untouched_when_nothing_is_removed(pypi):
    html = b'<a href="x">not a file</a>\n<a href="y">fine-2.0.tar.gz</a>'
    assert filters.pypi_simple(html, pypi) == (html, [])


def test_go_request_paths_it_does_not_filter():
    assert filters.go_request("github.com/x/mod/@v/v1.0.0.txt") is None
    assert filters.go_request("github.com/x/mod") is None


def test_go_list_with_everything_removed(go):
    assert filters.go_list(b"v1.2.3\n", "github.com/Evil/mod", go) == (b"", ["v1.2.3"])


# osvdb ----------------------------------------------------------------------


def test_normalize_is_pep503_for_pypi_only():
    assert osvdb.normalize("PyPI", "Foo_Bar.baz") == "foo-bar-baz"
    assert osvdb.normalize("npm", "Foo_Bar") == "Foo_Bar"


def test_affected_reads_each_kind_of_entry():
    data = {
        "affected": [
            {"package": {"ecosystem": "PyPI", "name": "other"}},
            {"package": {"ecosystem": "npm"}},
            {"package": {"ecosystem": "npm", "name": "whole"}, **WHOLE},
            {"package": {"ecosystem": "npm", "name": "some"}, "versions": ["1", "2"]},
            {
                "package": {"ecosystem": "npm", "name": "fixed"},
                "versions": ["1"],
                "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2"}]}],
            },
            {"package": {"ecosystem": "npm", "name": "bare"}},
        ]
    }
    assert list(osvdb.affected(data, "npm")) == [
        ("whole", None),
        ("some", {"1", "2"}),
        ("fixed", {"1"}),
        ("bare", None),
    ]


def test_feed_without_a_version_blocks_only_whole_packages(npm):
    assert npm.blocked("left-pad") is False
    assert npm.blocked("evil-pkg") is True


def test_withdrawn_entry_is_dropped_from_a_feed():
    f = feed("npm", entry("npm", "evil", **WHOLE))
    f._add("MAL-0", json.dumps({"withdrawn": "2026-01-01"}))
    f._index()
    assert f.blocked("evil") is False


def test_log_prefixes_osv(capsys):
    osvdb.log("hello")
    assert capsys.readouterr().out == "osv: hello\n"


def test_save_and_load_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(osvdb, "CACHE", tmp_path / "osv")
    f = feed(
        "npm", entry("npm", "evil", **WHOLE), entry("npm", "pad", versions=["1.0"])
    )
    f.synced = "2026-09-01T00:00:00Z"
    f.save()
    g = osvdb.Feed("npm")
    assert g.load() is True
    assert g.synced == "2026-09-01T00:00:00Z"
    assert g.blocked("evil") and g.blocked("pad", "1.0")
    assert "2 entries from the cache" in capsys.readouterr().out


def test_load_without_a_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(osvdb, "CACHE", tmp_path)
    assert osvdb.Feed("npm").load() is False


class Responses:
    """urlopen, answering each URL from a table; an exception is raised."""

    def __init__(self, table):
        self.table = table
        self.asked = []

    def __call__(self, url, timeout=None):
        self.asked.append(url)
        answer = self.table[url]
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer)


def all_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_sync_full_then_incremental(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(osvdb, "CACHE", tmp_path)
    base = osvdb.BASE + "/npm/"
    urls = Responses(
        {
            base + "all.zip": all_zip(
                {
                    "MAL-1.json": entry("npm", "evil", **WHOLE),
                    "MAL-2.json": entry("npm", "gone", **WHOLE),
                    "GHSA-1.json": entry("npm", "vuln", **WHOLE),
                    "MAL-3.txt": "not an entry",
                }
            ),
        }
    )
    monkeypatch.setattr(osvdb.urllib.request, "urlopen", urls)
    f = osvdb.Feed("npm")
    f.sync()
    assert sorted(f.entries) == ["MAL-1", "MAL-2"]
    assert f.blocked("evil") and not f.blocked("vuln")
    assert not (tmp_path / "npm.zip").exists()
    assert (tmp_path / "npm.json").exists()
    assert (
        "2 malicious entries (2 whole packages, 0 single versions)"
        in capsys.readouterr().out
    )

    f.synced = "2026-09-01T00:00:00Z"
    urls.table = {
        base + "modified_id.csv": (
            b"2026-09-20T00:00:00Z,MAL-4\n"
            b"2026-09-19T00:00:00Z,GHSA-9\n"
            b"2026-09-18T00:00:00Z,MAL-2\n"
            b"2026-09-17T00:00:00Z,MAL-1\n"
            b"2026-08-01T00:00:00Z,MAL-0\n"
        ),
        base + "MAL-4.json": entry("npm", "new-evil", versions=["2.0"]).encode(),
        base + "MAL-2.json": urllib.error.HTTPError(
            base + "MAL-2.json", 404, "Not Found", {}, None
        ),
        base + "MAL-1.json": json.dumps({"withdrawn": "2026-09-17"}).encode(),
    }
    f.sync()
    assert sorted(f.entries) == ["MAL-4"]
    assert f.blocked("new-evil", "2.0") and not f.blocked("evil")
    assert base + "MAL-0.json" not in urls.asked


def test_incremental_reads_to_the_end_and_skips_short_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(osvdb, "CACHE", tmp_path)
    base = osvdb.BASE + "/Go/"
    monkeypatch.setattr(
        osvdb.urllib.request,
        "urlopen",
        Responses(
            {
                base + "modified_id.csv": b"2026-09-20T00:00:00Z,MAL-7\n",
                base + "MAL-7.json": entry("Go", "x/mod", versions=["v1"]).encode(),
            }
        ),
    )
    f = osvdb.Feed("Go")
    f.synced = "2026-09-01T00:00:00Z"
    f.sync()
    assert f.blocked("x/mod", "v1")

    monkeypatch.setattr(
        osvdb.urllib.request,
        "urlopen",
        Responses({base + "modified_id.csv": b"short\n2026-09-30T00:00:00Z,MAL-8\n"}),
    )
    f.sync()
    assert "MAL-8" not in f.entries


def test_incremental_raises_other_http_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(osvdb, "CACHE", tmp_path)
    base = osvdb.BASE + "/PyPI/"
    monkeypatch.setattr(
        osvdb.urllib.request,
        "urlopen",
        Responses(
            {
                base + "modified_id.csv": b"2026-09-20T00:00:00Z,MAL-1\n",
                base + "MAL-1.json": urllib.error.HTTPError("u", 500, "boom", {}, None),
            }
        ),
    )
    f = osvdb.Feed("PyPI")
    f.synced = "2026-09-01T00:00:00Z"
    with pytest.raises(urllib.error.HTTPError):
        f.sync()


class Stop(Exception):
    pass


def test_refresh_forever_keeps_going_when_a_feed_fails(monkeypatch, capsys):
    calls = []

    class Good:
        ecosystem = "npm"
        synced = None

        def load(self):
            calls.append("load")

        def sync(self):
            calls.append("sync")

    class Bad(Good):
        ecosystem = "Go"
        synced = "2026-09-01"

        def sync(self):
            raise OSError("down")

    class NeverSynced(Bad):
        synced = None

    monkeypatch.setattr(
        osvdb, "FEEDS", {"npm": Good(), "Go": Bad(), "PyPI": NeverSynced()}
    )

    def sleep(seconds):
        assert seconds == 5
        raise Stop

    monkeypatch.setattr(osvdb.time, "sleep", sleep)
    with pytest.raises(Stop):
        osvdb.refresh_forever(5)
    out = capsys.readouterr().out
    assert calls == ["load", "load", "load", "sync"]
    assert "WARNING Go: sync failed (down); keeping the data from 2026-09-01" in out
    assert "keeping the data from nowhere" in out
