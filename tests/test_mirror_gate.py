"""Tests for images/mirror-gate/bin/mirror-gate.

Each request is handed straight to a Gate handler, with the client's side of
the connection in memory. The backend is a table of canned answers in place
of http.client.HTTPConnection, and the checksum database a stub urlopen, so
nothing here opens a socket.
"""

# cspell:words sumdb
from __future__ import annotations

import datetime
import email.message
import io
import json
import sys
import typing
import urllib.error
import urllib.parse

import pytest
from conftest import REPO_ROOT, load

sys.path.insert(0, str(REPO_ROOT / "images/mirror-gate/lib"))

import osvdb

BACKEND = "http://mirror-nexus:8081"
OLD = "2020-01-01T00:00:00Z"
YOUNG = (
    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
).isoformat()


@pytest.fixture
def mg(monkeypatch):
    monkeypatch.setenv(
        "MIRROR_ROUTES_DIR", str(REPO_ROOT / "images/mirror-gate/routes")
    )
    monkeypatch.delenv("MIRROR_BACKEND", raising=False)
    monkeypatch.delenv("MIN_AGE_DAYS", raising=False)
    module = load("images/mirror-gate/bin/mirror-gate", "mirror_gate")
    feeds = {e: osvdb.Feed(e) for e in osvdb.ECOSYSTEMS}
    for eco, name, versions in (
        ("npm", "evil-pkg", None),
        ("npm", "left-pad", ["1.3.1"]),
        ("PyPI", "evil-pkg", ["1.0"]),
        ("Go", "github.com/Evil/mod", ["v1.2.3"]),
    ):
        affected = {"package": {"ecosystem": eco, "name": name}}
        if versions:
            affected["versions"] = versions
        else:
            affected["ranges"] = [{"events": [{"introduced": "0"}]}]
        feeds[eco]._add(f"MAL-{name}", json.dumps({"affected": [affected]}))
        feeds[eco]._index()
    monkeypatch.setattr(osvdb, "FEEDS", feeds)
    return module


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.headers = list((headers or {}).items())
        self.stream = io.BytesIO(body)

    def getheaders(self):
        return self.headers

    def getheader(self, name):
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def read(self, n=-1):
        return self.stream.read(n)


class Backend:
    """http.client.HTTPConnection, answering each path from a table."""

    def __init__(self, table):
        self.table = table
        self.requests = []
        self.closed = 0

    def __call__(self, host, port, timeout):
        assert (host, port) == ("mirror-nexus", 8081)
        return self

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, headers))
        self.path = path

    def getresponse(self):
        answer = self.table[self.path]
        if isinstance(answer, Exception):
            raise answer
        answer.stream.seek(0)
        return answer

    def close(self):
        self.closed += 1


@pytest.fixture
def backend(monkeypatch):
    b = Backend({})
    monkeypatch.setattr("http.client.HTTPConnection", b)
    return b


class Reply:
    def __init__(self, raw):
        self.status, self.headers = None, {}
        head, _, self.body = raw.partition(b"\r\n\r\n")
        if not head:
            return
        lines = head.decode().split("\r\n")
        self.status = int(lines[0].split()[1])
        for line in lines[1:]:
            k, _, v = line.partition(": ")
            self.headers[k.lower()] = v


def ask(mg, method, path, registry=None, headers=None, wfile=None):
    cls = type("Handler", (mg.Gate,), {"registry": registry})
    h = cls.__new__(cls)
    h.command, h.path = method, path
    h.request_version = "HTTP/1.1"
    h.requestline = f"{method} {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    msg = email.message.Message()
    msg["Host"] = "gate:8081"
    for k, v in (headers or {}).items():
        msg[k] = v
    h.headers = msg
    h.wfile = wfile or io.BytesIO()
    getattr(h, f"do_{method}")()
    return Reply(h.wfile.getvalue())


def test_log_prints(mg, capsys):
    mg.log("a", 1)
    assert capsys.readouterr().out == "a 1\n"


def test_status(mg):
    r = ask(mg, "GET", "/_status")
    assert r.status == 200
    assert r.headers["content-type"] == "application/json"
    doc = json.loads(r.body)
    assert doc["backend"] == "nexus"
    assert doc["min_age_days"] == 0
    assert doc["osv"]["npm"] == {"synced": None, "entries": 2}


def test_reply_sends_extra_headers(mg):
    cls = type("Handler", (mg.Gate,), {})
    h = cls.__new__(cls)
    h.command, h.path, h.request_version = "GET", "/", "HTTP/1.1"
    h.requestline, h.client_address = "GET / HTTP/1.1", ("127.0.0.1", 0)
    h.wfile = io.BytesIO()
    h.reply(200, b"x", headers=[("X-Extra", "1")])
    r = Reply(h.wfile.getvalue())
    assert (r.headers["x-extra"], r.body) == ("1", b"x")


def test_head_has_no_body(mg):
    r = ask(mg, "HEAD", "/_status")
    assert r.status == 200 and r.body == b""
    assert int(r.headers["content-length"]) > 0


def test_unknown_path(mg):
    assert ask(mg, "GET", "/nothing").status == 404


def test_writes_are_refused(mg):
    for method in ("PUT", "DELETE", "PATCH", "POST"):
        r = ask(mg, method, "/npm/x")
        assert (r.status, r.body) == (405, b"mirror-gate: read only\n")
    assert ask(mg, "POST", "/v2/x", registry={"target": "/r/"}).status == 405


def test_registry_token_post_and_api(mg, backend):
    backend.table = {
        "/repository/docker-hub/v2/token": Resp(200, b"{}", {"Content-Length": "2"}),
        "/repository/docker-hub/v2/library/x/manifests/1": Resp(
            401,
            b"",
            {
                "Content-Length": "0",
                "WWW-Authenticate": f'Bearer realm="{BACKEND}/repository/docker-hub/v2/token"',
                "Connection": "keep-alive",
                "Docker-Distribution-Api-Version": "registry/2.0",
            },
        ),
    }
    reg = {"target": "/repository/docker-hub/", "port": 5000, "upstream": "docker.io"}
    r = ask(
        mg,
        "POST",
        "/v2/token",
        registry=reg,
        headers={"Accept-Encoding": "gzip", "X-Keep": "1"},
    )
    assert (r.status, r.body) == (200, b"{}")
    sent = backend.requests[0][2]
    assert sent == {"X-Keep": "1"}
    r = ask(mg, "GET", "/v2/library/x/manifests/1", registry=reg)
    assert r.status == 401
    assert r.headers["www-authenticate"] == 'Bearer realm="http://gate:8081/v2/token"'
    assert "connection" not in r.headers
    assert r.headers["docker-distribution-api-version"] == "registry/2.0"
    assert backend.closed == 2
    assert ask(mg, "GET", "/other", registry=reg).status == 404


def test_forwarded_plain_http(mg, backend):
    backend.table = {
        "/repository/apt-ubuntu-security/dists/x": Resp(
            200, b"ok", {"Content-Length": "2"}
        )
    }
    r = ask(mg, "GET", "http://security.ubuntu.com/ubuntu/dists/x")
    assert (r.status, r.body) == (200, b"ok")
    assert ask(mg, "GET", "http://example.com/x").status == 403


@pytest.mark.parametrize(
    "path",
    [
        "/apt/ubuntu/../../repository/npm/evil/-/evil-1.0.0.tgz",
        "/apt/ubuntu/%2e%2E/%2E./repository/npm/evil/-/evil-1.0.0.tgz",
        "/apt/ubuntu/..;x/..;/repository/npm/evil",
        "/apt/ubuntu/..\\..\\repository/npm/evil",
        "/npm/./left-pad",
        "http://archive.ubuntu.com/ubuntu/../../repository/pypi/simple/evil/",
    ],
)
def test_a_path_that_climbs_out_of_its_route_is_refused(mg, backend, capsys, path):
    r = ask(mg, "GET", path)
    assert (r.status, r.body) == (
        400,
        b"mirror-gate: a path may not climb out of its route\n",
    )
    assert backend.requests == []
    assert f"REFUSE {path}: climbs out of its route" in capsys.readouterr().out


def test_a_registry_path_that_climbs_is_refused(mg, backend):
    reg = {"target": "/repository/docker-hub/", "port": 5000, "upstream": "docker.io"}
    assert ask(mg, "GET", "/v2/../../npm/evil", registry=reg).status == 400
    assert backend.requests == []


def test_dots_inside_a_name_are_not_climbing(mg, backend):
    backend.table = {
        "/repository/apt-ubuntu/pool/a..b/x...deb?v=..": Resp(
            200, b"ok", {"Content-Length": "2"}
        )
    }
    assert ask(mg, "GET", "/apt/ubuntu/pool/a..b/x...deb?v=..").status == 200


def test_relay_streams_without_length_as_chunks(mg, backend):
    backend.table = {
        "/repository/apt-ubuntu/a": Resp(
            302,
            b"abc",
            {
                "Location": f"{BACKEND}/repository/apt-ubuntu/b",
                "Transfer-Encoding": "x",
            },
        )
    }
    r = ask(mg, "GET", "/apt/ubuntu/a")
    assert r.status == 302
    assert r.headers["location"] == "http://gate:8081/apt/ubuntu/b"
    assert r.headers["transfer-encoding"] == "chunked"
    assert r.body == b"3\r\nabc\r\n0\r\n\r\n"


def test_relay_head_with_length(mg, backend):
    backend.table = {
        "/repository/apt-ubuntu/a": Resp(200, b"", {"Content-Length": "9"})
    }
    r = ask(mg, "HEAD", "/apt/ubuntu/a")
    assert (r.status, r.headers["content-length"], r.body) == (200, "9", b"")


def test_backend_error_is_a_502(mg, backend, capsys):
    backend.table = {"/repository/apt-ubuntu/a": ConnectionRefusedError("refused")}
    r = ask(mg, "GET", "/apt/ubuntu/a")
    assert r.status == 502
    assert b"backend error: refused" in r.body
    assert "ERROR /apt/ubuntu/a: refused" in capsys.readouterr().out


def test_backend_error_with_the_client_gone(mg, backend):
    backend.table = {"/repository/apt-ubuntu/a": ConnectionRefusedError("refused")}

    class Gone(io.BytesIO):
        def write(self, data):
            raise BrokenPipeError

    r = ask(mg, "GET", "/apt/ubuntu/a", wfile=Gone())
    assert (r.status, r.body) == (None, b"")


# The checksum database ------------------------------------------------------


class Answer(io.BytesIO):
    status = 200
    headers: typing.ClassVar[dict[str, str]] = {"Content-Type": "text/x"}


def test_sumdb(mg, monkeypatch):
    asked = []

    def urlopen(url, timeout):
        asked.append(url)
        if url.endswith("missing"):
            raise urllib.error.HTTPError(url, 410, "Gone", {}, io.BytesIO(b"gone"))
        return Answer(b"tree")

    monkeypatch.setattr(mg.urllib.request, "urlopen", urlopen)
    assert ask(mg, "GET", "/go/sumdb/evil.example/latest").status == 404
    r = ask(mg, "GET", "/go/sumdb/sum.golang.org/supported")
    assert (r.status, r.body) == (200, b"")
    r = ask(mg, "GET", "/go/sumdb/sum.golang.org/latest")
    assert (r.status, r.body, r.headers["content-type"]) == (200, b"tree", "text/x")
    r = ask(mg, "GET", "/go/sumdb/sum.golang.org/missing")
    assert (r.status, r.body) == (410, b"gone")
    assert asked == ["https://sum.golang.org/latest", "https://sum.golang.org/missing"]


# npm ------------------------------------------------------------------------

NPM = "/repository/npm/"


def packument(name, times):
    return json.dumps(
        {
            "name": name,
            "dist-tags": {"latest": max(times)},
            "versions": {
                v: {"dist": {"tarball": f"{BACKEND}{NPM}{name}/-/{name}-{v}.tgz"}}
                for v in times
            },
            "time": times,
        }
    ).encode()


def test_npm_malicious_tarball_refused(mg, backend, capsys):
    r = ask(mg, "GET", "/npm/left-pad/-/left-pad-1.3.1.tgz")
    assert r.status == 403
    assert b"left-pad@1.3.1 refused: listed as malicious in OSV" in r.body
    assert "REFUSE /npm/left-pad/-/left-pad-1.3.1.tgz" in capsys.readouterr().out
    assert backend.requests == []


def test_npm_tarball_passes_without_min_age(mg, backend):
    backend.table = {
        NPM + "left-pad/-/left-pad-1.3.0.tgz": Resp(
            200, b"tgz", {"Content-Length": "3"}
        )
    }
    assert ask(mg, "GET", "/npm/left-pad/-/left-pad-1.3.0.tgz").body == b"tgz"


def test_npm_tarball_age(mg, backend, monkeypatch):
    monkeypatch.setattr(mg, "MIN_AGE", 7.0)
    backend.table = {
        NPM + "@scope%2Fpkg": Resp(
            200, packument("@scope/pkg", {"1.0.0": OLD, "2.0.0": YOUNG})
        ),
        NPM + "@scope/pkg/-/pkg-1.0.0.tgz": Resp(200, b"old", {"Content-Length": "3"}),
        NPM + "gone": Resp(404, b""),
        NPM + "gone/-/gone-1.0.0.tgz": Resp(404, b"", {"Content-Length": "0"}),
    }
    r = ask(mg, "GET", "/npm/@scope/pkg/-/pkg-2.0.0.tgz")
    assert r.status == 403 and b"published less than 7 days ago" in r.body
    assert ask(mg, "GET", "/npm/@scope/pkg/-/pkg-1.0.0.tgz").body == b"old"
    assert ask(mg, "GET", "/npm/gone/-/gone-1.0.0.tgz").status == 404


def test_npm_packument_filtered(mg, backend, capsys):
    backend.table = {
        NPM + "left-pad": Resp(
            200, packument("left-pad", {"1.3.0": OLD, "1.3.1": OLD})
        ),
    }
    backend.table[NPM + "left-pad?write=true"] = backend.table[NPM + "left-pad"]
    r = ask(mg, "HEAD", "/npm/left-pad")
    assert r.status == 200 and r.body == b""
    assert backend.requests[0][0] == "GET"
    assert backend.requests[0][2]["Accept"] == "application/json"
    r = ask(mg, "GET", "/npm/left-pad?write=true")
    doc = json.loads(r.body)
    assert list(doc["versions"]) == ["1.3.0"]
    assert (
        doc["versions"]["1.3.0"]["dist"]["tarball"]
        == "http://gate:8081/npm/left-pad/-/left-pad-1.3.0.tgz"
    )
    assert int(r.headers["content-length"]) == len(r.body)
    assert "FILTER /npm/left-pad?write=true: left out 1.3.1" in capsys.readouterr().out


def test_npm_packument_not_found_is_relayed(mg, backend):
    backend.table = {NPM + "nothing": Resp(404, b"no", {"Content-Length": "2"})}
    assert ask(mg, "GET", "/npm/nothing").status == 404


def test_npm_other_paths_pass(mg, backend):
    backend.table = {
        NPM + "-/v1/search?text=x": Resp(200, b"{}", {"Content-Length": "2"})
    }
    assert ask(mg, "GET", "/npm/-/v1/search?text=x").body == b"{}"


def test_npm_packument_unchanged_logs_nothing(mg, backend, capsys):
    backend.table = {NPM + "fine": Resp(200, packument("fine", {"1.0.0": OLD}))}
    assert json.loads(ask(mg, "GET", "/npm/fine").body)["name"] == "fine"
    assert "FILTER" not in capsys.readouterr().out


def test_many_removed_versions_are_cut_short_in_the_log(mg, backend, capsys):
    times = {f"1.0.{i}": OLD for i in range(12)}
    backend.table = {NPM + "evil-pkg": Resp(200, packument("evil-pkg", times))}
    ask(mg, "GET", "/npm/evil-pkg")
    assert capsys.readouterr().out.rstrip().endswith(" ...")


# PyPI -----------------------------------------------------------------------

PYPI = "/repository/pypi/"


def test_pypi_index_filtered(mg, backend):
    html = b'<a href="x/evil_pkg-1.0.tar.gz">evil_pkg-1.0.tar.gz</a><br/>\n<a href="y">evil_pkg-1.1.tar.gz</a>'
    backend.table = {PYPI + "simple/evil-pkg/": Resp(200, html)}
    r = ask(mg, "GET", "/pypi/simple/evil-pkg/")
    assert b"1.0.tar.gz" not in r.body and b"1.1.tar.gz" in r.body


def test_pypi_downloads(mg, backend):
    r = ask(mg, "GET", "/pypi/packages/a/b/evil_pkg-1.0.tar.gz#sha256=1")
    assert r.status == 403 and b"evil_pkg==1.0" in r.body
    backend.table = {
        PYPI + "packages/a/b/evil_pkg-1.1.tar.gz": Resp(
            200, b"ok", {"Content-Length": "2"}
        ),
        PYPI + "packages/a/b/README": Resp(200, b"ok", {"Content-Length": "2"}),
    }
    assert ask(mg, "GET", "/pypi/packages/a/b/evil_pkg-1.1.tar.gz").body == b"ok"
    assert ask(mg, "GET", "/pypi/packages/a/b/README").body == b"ok"


# Go -------------------------------------------------------------------------

GO = "/repository/go/"
MOD = "github.com/!evil/mod"


def test_go_list_filtered(mg, backend):
    backend.table = {GO + MOD + "/@v/list": Resp(200, b"v1.2.2\nv1.2.3\n")}
    assert ask(mg, "GET", f"/go/{MOD}/@v/list").body == b"v1.2.2\n"


def test_go_malicious_version_refused(mg, backend):
    r = ask(mg, "GET", f"/go/{MOD}/@v/v1.2.3.zip")
    assert r.status == 403 and b"github.com/Evil/mod@v1.2.3" in r.body


def test_go_info_and_latest(mg, backend, monkeypatch):
    monkeypatch.setattr(mg, "MIN_AGE", 7.0)
    backend.table = {
        GO + MOD + "/@latest": Resp(
            200, json.dumps({"Version": "v1.2.3", "Time": OLD}).encode()
        ),
        GO + MOD + "/@v/v1.2.4.info": Resp(200, json.dumps({"Time": YOUNG}).encode()),
        GO + MOD + "/@v/v1.2.2.info": Resp(
            200, json.dumps({"Version": "v1.2.2", "Time": OLD}).encode()
        ),
    }
    r = ask(mg, "GET", f"/go/{MOD}/@latest")
    assert r.status == 403 and b"malicious" in r.body
    r = ask(mg, "GET", f"/go/{MOD}/@v/v1.2.4.info")
    assert r.status == 403 and b"less than 7 days" in r.body
    r = ask(mg, "GET", f"/go/{MOD}/@v/v1.2.2.info")
    assert json.loads(r.body)["Version"] == "v1.2.2"


def test_go_mod_and_zip_age(mg, backend, monkeypatch):
    backend.table = {
        GO + MOD + "/@v/v1.0.0.mod": Resp(200, b"module x", {"Content-Length": "8"}),
        GO + MOD + "/@v/v1.0.0.info": Resp(200, json.dumps({"Time": OLD}).encode()),
        GO + MOD + "/@v/v2.0.0.info": Resp(200, json.dumps({"Time": YOUNG}).encode()),
        GO + MOD + "/@v/v3.0.0.info": Resp(200, b"not json"),
        GO + MOD + "/@v/v3.0.0.zip": Resp(200, b"zip", {"Content-Length": "3"}),
    }
    assert ask(mg, "GET", f"/go/{MOD}/@v/v1.0.0.mod").body == b"module x"
    monkeypatch.setattr(mg, "MIN_AGE", 7.0)
    assert ask(mg, "GET", f"/go/{MOD}/@v/v1.0.0.mod").body == b"module x"
    r = ask(mg, "GET", f"/go/{MOD}/@v/v2.0.0.zip")
    assert r.status == 403 and b"github.com/Evil/mod@v2.0.0" in r.body
    assert ask(mg, "GET", f"/go/{MOD}/@v/v3.0.0.zip").body == b"zip"


def test_go_other_paths_pass(mg, backend):
    backend.table = {GO + "x/@v/v1.txt": Resp(200, b"ok", {"Content-Length": "2"})}
    assert ask(mg, "GET", "/go/x/@v/v1.txt").body == b"ok"


# Starting up ----------------------------------------------------------------


def test_serve(mg, monkeypatch, capsys):
    servers = []

    class Server:
        def __init__(self, address, handler):
            self.address, self.handler = address, handler
            servers.append(self)

        def serve_forever(self):
            self.served = True

    monkeypatch.setattr(mg.http.server, "ThreadingHTTPServer", Server)
    mg.serve(8081)
    mg.serve(5000, {"upstream": "docker.io"})
    assert [s.address for s in servers] == [
        ("0.0.0.0", 8081),  # noqa: S104 (what the gate binds, checked here)
        ("0.0.0.0", 5000),  # noqa: S104
    ]
    assert servers[0].handler.registry is None
    assert servers[1].handler.registry == {"upstream": "docker.io"}
    assert all(s.daemon_threads and s.served for s in servers)
    out = capsys.readouterr().out
    assert "listening on 8081\n" in out and "listening on 5000 for docker.io" in out


def test_main(mg, monkeypatch):
    started, served, handlers = [], [], {}

    class Thread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            started.append((self.target, self.args))

    monkeypatch.setattr(mg.threading, "Thread", Thread)
    monkeypatch.setattr(
        mg.signal, "signal", lambda sig, fn: handlers.setdefault(sig, fn)
    )
    monkeypatch.setattr(mg, "serve", lambda port, registry=None: served.append(port))
    monkeypatch.setenv("OSV_REFRESH_SECONDS", "60")
    monkeypatch.setenv("MIRROR_PORT", "9000")
    mg.main()
    assert started[0] == (osvdb.refresh_forever, (60,))
    assert [args[0] for _, args in started[1:]] == [5000, 5001]
    assert served == [9000]
    with pytest.raises(SystemExit) as e:
        handlers[mg.signal.SIGTERM]()
    assert e.value.code == 0


def test_min_age_from_the_environment(monkeypatch):
    monkeypatch.setenv(
        "MIRROR_ROUTES_DIR", str(REPO_ROOT / "images/mirror-gate/routes")
    )
    monkeypatch.setenv("MIN_AGE_DAYS", "3")
    assert load("images/mirror-gate/bin/mirror-gate", "mirror_gate_aged").MIN_AGE == 3.0
    assert urllib.parse.urlsplit(BACKEND).port == 8081
