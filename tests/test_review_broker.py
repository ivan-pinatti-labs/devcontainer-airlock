"""Tests for images/review-broker/broker.py.

The refuse and withhold cases images/review-broker/test_broker.py runs in
the image build, and the rest of the broker, as pytest cases run from the
tree. The reviewer is a stub script; nothing reaches the network, and no
socket listens outside the test's own directory.
"""

# cspell:words socketpair
from __future__ import annotations

import base64
import io
import json
import re
import socket
import stat
import subprocess
import sys
import tarfile
import threading

import pytest
from conftest import load

TOKEN = "tok-" + "A1b2C3d4" * 8
REFRESHED = "tok-" + "Z9y8X7w6" * 8

# The stub reviewer: records what it was given, then does what the file `do`
# beside it says (the broker passes it no environment of ours).
STUB = """#!{python}
import json, os, sys
argv = sys.argv[1:]
here = {here!r}
record = {{
    "argv": argv, "stdin": sys.stdin.read(), "env": dict(os.environ),
    "home_files": sorted(os.listdir(os.environ["CODEX_HOME"])),
    "cwd": os.getcwd(), "tree_files": sorted(os.listdir(".")),
}}
json.dump(record, open(os.path.join(here, "record.json"), "w"))
answer = argv[argv.index("--output-last-message") + 1]
do = open(os.path.join(here, "do")).read() if os.path.exists(os.path.join(here, "do")) else ""
auth = os.path.join(os.environ["CODEX_HOME"], "auth.json")
if do == "leak":
    open(answer, "w").write("token: " + json.load(open(auth))["tokens"]["access_token"][5:40])
elif do == "leak-err":
    sys.stderr.write(json.load(open(auth))["tokens"]["access_token"])
    sys.exit(1)
elif do == "refresh":
    json.dump({{"tokens": {{"access_token": {refreshed!r}}}}}, open(auth, "w"))
    open(answer, "w").write("VERDICT: CLEAN\\n")
elif do == "fail":
    sys.stderr.write("x" * 5000 + "stub: boom\\n")
    sys.exit(3)
elif do == "silent":
    pass
else:
    open(answer, "w").write("Looks fine.\\n\\n**VERDICT: CLEAN**\\n")
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    login = tmp_path / "login"
    login.mkdir()
    (login / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": TOKEN, "n": 1, "l": ["short", TOKEN]}})
    )
    stub = tmp_path / "codex"
    stub.write_text(
        STUB.format(python=sys.executable, here=str(tmp_path), refreshed=REFRESHED)
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("REVIEW_LOGIN_DIR", str(login))
    monkeypatch.setenv("REVIEW_CODEX", str(stub))
    monkeypatch.setenv("REVIEW_PER_HOUR", "100")
    monkeypatch.setenv("REVIEW_MODELS", "gpt-x, ,gpt-y")
    monkeypatch.setenv("REVIEW_MAX_BYTES", "100000")
    monkeypatch.setenv("REVIEW_BROKER_DIR", str(tmp_path / "sock"))
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8888")
    monkeypatch.setenv("SECRET_OF_OURS", "nope")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    broker = load("images/review-broker/broker.py", "review_broker")
    monkeypatch.setattr(broker.tempfile, "tempdir", str(tmp_path))

    class Env:
        pass

    e = Env()
    e.broker, e.tmp, e.login = broker, tmp_path, login
    e.do = lambda what: (tmp_path / "do").write_text(what)
    e.record = lambda: json.loads((tmp_path / "record.json").read_text())
    return e


def tree(files=None, extra=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, body in (
            files or {"README.md": b"hello\n", "src/a.py": b"x = 1\n"}
        ).items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        for info in extra:
            tar.addfile(info)
    return base64.b64encode(buf.getvalue()).decode()


def req(**kw):
    r = {
        "reviewer": "codex",
        "model": "",
        "effort": "medium",
        "prompt": "Review.\n+diff\n",
        "tree": tree(),
    }
    r.update(kw)
    return r


def test_a_review_runs_blind_and_returns_the_answer(env, capsys):
    r = env.broker.handle(req(), 1)
    assert r == {"rc": 0, "out": "Looks fine.\n\n**VERDICT: CLEAN**\n", "err": ""}
    rec = env.record()
    argv = rec["argv"]
    assert argv[:5] == [
        "exec",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
    ]
    for flag in (
        "project_doc_max_bytes=0",
        'developer_instructions=""',
        "skills.include_instructions=false",
        'web_search="disabled"',
        'model_reasoning_effort="medium"',
    ):
        assert flag in argv
    for feature in env.broker.DISABLED:
        assert argv[argv.index(feature) - 1] == "--disable"
    assert argv[-1] == "-"
    assert not any(a.startswith("model=") for a in argv)
    assert rec["stdin"] == "Review.\n+diff\n"
    assert rec["home_files"] == ["auth.json"]
    assert rec["tree_files"] == ["README.md", "src"]
    assert rec["env"]["HTTPS_PROXY"] == "http://proxy:8888"
    assert "SECRET_OF_OURS" not in rec["env"]
    assert not (env.tmp / rec["cwd"]).exists()
    out = capsys.readouterr().out
    assert "REVIEW 1 codex default medium prompt=14" in out
    assert "DONE 1 rc=0" in out and "VERDICT: CLEAN" in out


def test_an_allowed_model_is_passed(env):
    assert env.broker.handle(req(model="gpt-y"), 1)["rc"] == 0
    assert 'model="gpt-y"' in env.record()["argv"]
    assert env.broker.MODELS == {"", "gpt-x", "gpt-y"}


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"reviewer": "claude"}, "reviewer 'claude' is not served here"),
        (
            {"model": "gpt-z"},
            "model 'gpt-z' is not allowed (REVIEW_MODELS: gpt-x, gpt-y",
        ),
        ({"effort": "max"}, "effort 'max' is not one of high, low, medium"),
        ({"prompt": "  "}, "no prompt"),
        ({"prompt": 3}, "no prompt"),
        ({"tree": None}, "no tree"),
    ],
)
def test_what_a_request_may_choose(env, change, why):
    r = env.broker.handle(req(**change), 1)
    assert r["rc"] == 126 and why in r["err"]


def test_no_models_allowed_says_none(env, monkeypatch):
    monkeypatch.setattr(env.broker, "MODELS", frozenset({""}))
    assert "REVIEW_MODELS: none" in env.broker.handle(req(model="gpt-x"), 1)["err"]


def _link():
    i = tarfile.TarInfo("evil")
    i.type, i.linkname = tarfile.SYMTYPE, "/etc/passwd"
    return i


def _device():
    i = tarfile.TarInfo("dev")
    i.type = tarfile.CHRTYPE
    return i


@pytest.mark.parametrize(
    ("t", "why"),
    [
        (lambda: tree(extra=[_link()]), "not a file or folder"),
        (lambda: tree(extra=[_device()]), "not a file or folder"),
        (lambda: tree({"/etc/x": b"x"}), "unsafe path"),
        (lambda: tree({"a/../../x": b"x"}), "unsafe path"),
        (
            lambda: tree({".codex/config.toml": b"x"}),
            "Codex project configuration is refused",
        ),
        (
            lambda: tree({"sub/.codex/config.toml": b"x"}),
            "Codex project configuration is refused",
        ),
        (lambda: tree({"big": b"0" * 200000}), "over REVIEW_MAX_BYTES"),
        (lambda: base64.b64encode(b"not a tar").decode(), "refused:"),
    ],
)
def test_what_a_tree_may_hold(env, t, why):
    r = env.broker.handle(req(tree=t()), 1)
    assert r["rc"] == 126 and why in r["err"]


def test_a_tree_that_is_not_base64(env):
    assert env.broker.handle(req(tree="!!!"), 1)["rc"] == 2


def test_a_folder_in_the_tree_is_fine(env):
    folder = tarfile.TarInfo("docs")
    folder.type = tarfile.DIRTYPE
    assert env.broker.handle(req(tree=tree(extra=[folder])), 1)["rc"] == 0


@pytest.mark.parametrize("what", ["leak", "leak-err"])
def test_a_reply_holding_the_login_is_withheld(env, what):
    env.do(what)
    r = env.broker.handle(req(), 1)
    assert r["rc"] == 126 and "withheld" in r["err"]
    assert TOKEN[5:40] not in r["out"] + r["err"]


def test_a_refreshed_login_is_saved(env, capsys):
    env.do("refresh")
    assert env.broker.handle(req(), 1)["rc"] == 0
    assert (
        json.loads((env.login / "auth.json").read_text())["tokens"]["access_token"]
        == REFRESHED
    )
    assert oct((env.login / "auth.json").stat().st_mode & 0o777) == "0o600"
    assert "LOGIN refreshed" in capsys.readouterr().out


def test_a_login_that_cannot_be_saved_is_logged(env, monkeypatch, capsys):
    def fail(*_):
        raise OSError("read only")

    monkeypatch.setattr(env.broker, "save_login", fail)
    assert env.broker.handle(req(), 1)["rc"] == 0
    assert "LOGIN not saved: read only" in capsys.readouterr().out


def test_the_reviewer_failing_comes_back_with_the_tail_of_its_log(env):
    env.do("fail")
    r = env.broker.handle(req(), 1)
    assert r["rc"] == 3 and r["err"].endswith("stub: boom\n") and len(r["err"]) == 4000


def test_no_answer_is_an_empty_one(env, capsys):
    env.do("silent")
    assert env.broker.handle(req(), 1) == {"rc": 0, "out": "", "err": ""}
    assert re.search(r"^DONE 1 rc=0 \d+s -$", capsys.readouterr().out, re.MULTILINE)


def test_a_review_that_takes_too_long(env, monkeypatch):
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("codex", 1)

    monkeypatch.setattr(env.broker.subprocess, "run", slow)
    r = env.broker.handle(req(), 1)
    assert r["rc"] == 124 and "longer than" in r["err"]


def test_the_hourly_cap_counts_only_reviews_that_ran(env, monkeypatch):
    monkeypatch.setattr(env.broker, "PER_HOUR", 2)
    clock = [1000.0]
    monkeypatch.setattr(env.broker.time, "monotonic", lambda: clock[0])
    env.broker.handle(req(effort="max"), 1)
    env.broker.handle(req(tree=tree({"/x": b"x"})), 2)
    assert env.broker.handle(req(), 3)["rc"] == 0
    assert env.broker.handle(req(), 4)["rc"] == 0
    r = env.broker.handle(req(), 5)
    assert r["rc"] == 126 and "REVIEW_PER_HOUR" in r["err"]
    clock[0] += 3601
    assert env.broker.handle(req(), 6)["rc"] == 0


def test_one_review_at_a_time(env, monkeypatch, capsys):
    monkeypatch.setattr(env.broker, "TIMEOUT", 0)
    env.broker._one_at_a_time.acquire()
    try:
        r = env.broker.handle(req(), 7)
    finally:
        env.broker._one_at_a_time.release()
    assert r["rc"] == 124 and "still runs" in r["err"]
    assert "BUSY 7" in capsys.readouterr().out


def test_no_login(env):
    (env.login / "auth.json").unlink()
    r = env.broker.handle(req(), 1)
    assert r["rc"] == 125 and "review-login" in r["err"]


def test_last_line_of_nothing(env):
    assert env.broker.last_line("\n \n") == "-"


def serve(broker, payload):
    a, b = socket.socketpair()
    t = threading.Thread(target=broker.serve, args=(a, 9))
    t.start()
    b.sendall(payload)
    b.shutdown(socket.SHUT_WR)
    raw = b"".join(iter(lambda: b.recv(65536), b""))
    t.join()
    b.close()
    return json.loads(raw)


def test_serve_answers_a_request(env):
    assert serve(env.broker, (json.dumps(req()) + "\n").encode())["rc"] == 0


@pytest.mark.parametrize("payload", [b"not json\n", b"[1]\n"])
def test_serve_refuses_a_malformed_request(env, payload):
    assert serve(env.broker, payload)["rc"] == 2


def test_serve_refuses_a_request_too_large(env, monkeypatch):
    monkeypatch.setattr(env.broker, "MAX_LINE", 10)
    assert serve(env.broker, b'{"reviewer": "codex", "x": 1}\n')["rc"] == 126


def test_serve_logs_a_client_gone(env, capsys):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def makefile(self, _mode):
            return io.BytesIO((json.dumps(req(effort="max")) + "\n").encode())

        def sendall(self, _data):
            raise OSError("gone")

    env.broker.serve(Conn(), 4)
    assert "GONE 4" in capsys.readouterr().out


class Stop(Exception):
    """Ends main()'s accept loop."""


class FakeSocket:
    def __init__(self, family):
        self.path, self.accepted = None, False

    def bind(self, path):
        self.path = path
        open(path, "w").close()

    def listen(self):
        pass

    def accept(self):
        if self.accepted:
            raise Stop
        self.accepted = True
        return "conn", None


@pytest.mark.parametrize("stale", [True, False])
@pytest.mark.parametrize("logged_in", [True, False])
def test_main_listens_and_serves(env, monkeypatch, capsys, stale, logged_in):
    sock = env.tmp / "sock"
    sock.mkdir()
    if stale:
        (sock / "review.sock").write_text("left from a previous run")
    if not logged_in:
        (env.login / "auth.json").unlink()
    started = []

    class Thread:
        def __init__(self, target, args, daemon):
            assert target is env.broker.serve and daemon
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(env.broker.socket, "socket", FakeSocket)
    monkeypatch.setattr(env.broker.threading, "Thread", Thread)
    with pytest.raises(Stop):
        env.broker.main()
    assert started == [("conn", 1)]
    assert (sock / "review.sock").read_text() == ""
    assert oct((sock / "review.sock").stat().st_mode & 0o777) == "0o660"
    out = capsys.readouterr().out
    assert f"listening on {sock}/review.sock" in out
    assert ("a login" if logged_in else "no login yet") in out
    assert "models: default, gpt-x, gpt-y, 100 an hour, 900s each" in out


def test_main_needs_its_folder(env, capsys):
    with pytest.raises(SystemExit) as e:
        env.broker.main()
    assert e.value.code == 1
    assert "the host mounts it" in capsys.readouterr().err
