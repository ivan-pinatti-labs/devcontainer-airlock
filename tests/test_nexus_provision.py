"""Tests for images/mirror-gate/backends/nexus/provision.

No Nexus and no network: `call` is replaced by a table of answers for the
steps, and `call` itself is tested against a stub opener.
"""

# cspell:words extdirect coreui
from __future__ import annotations

import io
import json
import urllib.error

import pytest
from conftest import load

SCRIPT = "images/mirror-gate/backends/nexus/provision"


@pytest.fixture
def prov(tmp_path, monkeypatch):
    module = load(SCRIPT, "nexus_provision")
    monkeypatch.setattr(module, "KEPT", tmp_path / "kept" / "password")
    monkeypatch.setattr(module, "INITIAL", tmp_path / "admin.password")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


class Answer:
    def __init__(self, status, text):
        self.status = status
        self.text = text

    def read(self):
        return self.text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        return self.answer


def test_call_sends_json_with_basic_auth(prov, monkeypatch):
    opener = Opener(Answer(200, ' {"a": 1}'))
    monkeypatch.setattr(prov.urllib.request, "build_opener", lambda *_: opener)
    assert prov.call("POST", "/x", {"k": 1}, "pw", {"X-One": "1"}) == (200, {"a": 1})
    req = opener.requests[0]
    assert req.full_url == prov.URL + "/x"
    assert req.data == b'{"k": 1}'
    assert req.get_header("Content-type") == "application/json"
    assert req.get_header("Authorization") == "Basic YWRtaW46cHc="
    assert req.get_header("X-one") == "1"


def test_call_raw_text_without_auth(prov, monkeypatch):
    opener = Opener(Answer(204, "plain"))
    monkeypatch.setattr(prov.urllib.request, "build_opener", lambda *_: opener)
    assert prov.call("PUT", "/p", "secret", raw=True) == (204, "plain")
    assert opener.requests[0].data == b"secret"
    assert opener.requests[0].get_header("Content-type") == "text/plain"
    assert opener.requests[0].get_header("Authorization") is None
    assert prov.call("GET", "/g")[1] == "plain"
    assert opener.requests[1].data is None


def test_log(prov, capsys):
    prov.log("hi")
    assert capsys.readouterr().err == "provision: hi\n"


def test_wait_up_retries_until_writable(prov, monkeypatch):
    answers = [urllib.error.URLError("refused"), OSError("reset"), (503, ""), (200, "")]

    def call(method, path, *a, **k):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(prov, "call", call)
    prov.wait_up()
    assert answers == []


def test_wait_up_gives_up(prov, monkeypatch, capsys):
    monkeypatch.setattr(prov, "call", lambda *a, **k: (503, ""))
    with pytest.raises(SystemExit) as e:
        prov.wait_up()
    assert e.value.code == 1
    assert "six minutes" in capsys.readouterr().err


def http_error(code=401):
    return urllib.error.HTTPError("http://nexus/x", code, "no", {}, io.BytesIO(b""))


def test_works(prov, monkeypatch):
    monkeypatch.setattr(prov, "call", lambda *a, **k: (200, ""))
    assert prov.works("pw") is True

    def refuse(*a, **k):
        raise http_error()

    monkeypatch.setattr(prov, "call", refuse)
    assert prov.works("pw") is False


def test_admin_password_keeps_a_working_one(prov, monkeypatch):
    prov.KEPT.parent.mkdir()
    prov.KEPT.write_text("kept\n")
    monkeypatch.setattr(prov, "works", lambda pw: pw == "kept")
    assert prov.admin_password() == "kept"


def test_admin_password_replaces_the_first_one(prov, monkeypatch, capsys):
    prov.KEPT.parent.mkdir()
    prov.KEPT.write_text("stale")
    prov.INITIAL.write_text("first\n")
    monkeypatch.setattr(prov, "works", lambda pw: False)
    monkeypatch.setattr(prov.secrets, "token_urlsafe", lambda n: "new-password")
    calls = []
    monkeypatch.setattr(prov, "call", lambda *a, **k: calls.append((a, k)) or (204, ""))
    assert prov.admin_password() == "new-password"
    assert calls == [
        (
            (
                "PUT",
                "/service/rest/v1/security/users/admin/change-password",
                "new-password",
                "first",
            ),
            {"raw": True},
        )
    ]
    assert prov.KEPT.read_text() == "new-password"
    assert prov.KEPT.stat().st_mode & 0o777 == 0o600
    assert "admin password replaced" in capsys.readouterr().err


def test_admin_password_without_any(prov, capsys):
    with pytest.raises(SystemExit) as e:
        prov.admin_password()
    assert e.value.code == 1
    assert "no working admin password" in capsys.readouterr().err


def test_extdirect_sends_the_csrf_pair(prov, monkeypatch):
    seen = []

    def call(method, path, body, password, headers):
        seen.append((method, path, body, password, headers))
        return 200, {"result": {"success": True}}

    monkeypatch.setattr(prov, "call", call)
    monkeypatch.setattr(prov.secrets, "token_hex", lambda n: "tok")
    prov.extdirect("pw", "coreui_X", "update", [1])
    method, path, body, password, headers = seen[0]
    assert (method, path, password) == ("POST", "/service/extdirect", "pw")
    assert body == {
        "action": "coreui_X",
        "method": "update",
        "type": "rpc",
        "tid": 1,
        "data": [1],
    }
    assert headers["NX-ANTI-CSRF-TOKEN"] == "tok"
    assert headers["Cookie"] == "NX-ANTI-CSRF-TOKEN=tok"


@pytest.mark.parametrize("answer", [{"result": {"success": False}}, "an html page"])
def test_extdirect_refused(prov, monkeypatch, capsys, answer):
    monkeypatch.setattr(prov, "call", lambda *a, **k: (200, answer))
    with pytest.raises(SystemExit) as e:
        prov.extdirect("pw", "coreui_X", "update", [])
    assert e.value.code == 1
    assert "coreui_X.update refused" in capsys.readouterr().err


def test_main_usage(prov, capsys):
    assert prov.main([]) == 1
    assert "Usage:" in capsys.readouterr().err


class Nexus:
    """Answers for the steps main() takes, recording each call."""

    def __init__(self, eula, have):
        self.eula = eula
        self.have = have
        self.calls = []

    def __call__(self, method, path, body=None, password=None, headers=None, raw=False):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/service/rest/v1/system/eula"):
            return 200, self.eula
        if (method, path) == ("GET", "/service/rest/v1/repositories"):
            return 200, self.have
        if path == "/service/extdirect":
            return 200, {"result": {"success": True}}
        return 204, ""


def wanted(tmp_path):
    repos = [
        {
            "format": "npm",
            "spec": {
                "name": "npm",
                "proxy": {"remoteUrl": "https://registry.npmjs.org"},
            },
        },
        {
            "format": "docker",
            "spec": {
                "name": "docker-hub",
                "proxy": {"remoteUrl": "https://registry-1.docker.io"},
            },
        },
    ]
    path = tmp_path / "repositories.json"
    path.write_text(json.dumps(repos))
    return str(path)


def test_main_provisions_a_fresh_backend(prov, monkeypatch, tmp_path, capsys):
    nexus = Nexus(
        {"accepted": False, "disclaimer": "terms"},
        [
            {"name": "npm", "type": "proxy", "format": "npm"},
            {"name": "maven-releases", "type": "hosted", "format": "maven2"},
        ],
    )
    monkeypatch.setattr(prov, "call", nexus)
    monkeypatch.setattr(prov, "wait_up", lambda: None)
    monkeypatch.setattr(prov, "admin_password", lambda: "pw")
    monkeypatch.setenv("UPSTREAM_PROXY", "egress-proxy:3128")
    assert prov.main([wanted(tmp_path)]) == 0
    done = [(m, p) for m, p, _ in nexus.calls]
    assert ("POST", "/service/rest/v1/system/eula") in done
    assert ("PUT", "/service/rest/v1/repositories/npm/proxy/npm") in done
    assert ("POST", "/service/rest/v1/repositories/docker/proxy") in done
    assert ("DELETE", "/service/rest/v1/repositories/maven-releases") in done
    proxy = next(b for m, p, b in nexus.calls if p == "/service/extdirect")["data"][0]
    assert (proxy["httpHost"], proxy["httpPort"]) == ("egress-proxy", 3128)
    err = capsys.readouterr().err
    assert "EULA accepted" in err
    assert "created docker proxy docker-hub of https://registry-1.docker.io" in err
    assert "deleted hosted maven2 repository maven-releases" in err
    assert "ready: 2 proxy repositories" in err


def test_main_on_a_provisioned_backend_changes_nothing_extra(
    prov, monkeypatch, tmp_path
):
    nexus = Nexus(
        {"accepted": True},
        [{"name": "npm"}, {"name": "docker-hub"}],
    )
    monkeypatch.setattr(prov, "call", nexus)
    monkeypatch.setattr(prov, "wait_up", lambda: None)
    monkeypatch.setattr(prov, "admin_password", lambda: "pw")
    monkeypatch.setenv("UPSTREAM_PROXY", "egress-proxy:3128")
    assert prov.main([wanted(tmp_path)]) == 0
    methods = {(m, p) for m, p, _ in nexus.calls}
    assert ("POST", "/service/rest/v1/system/eula") not in methods
    assert not any(m in ("POST", "DELETE") and "repositories" in p for m, p in methods)
