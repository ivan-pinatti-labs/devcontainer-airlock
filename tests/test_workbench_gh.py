"""Tests for images/workbench/bin/gh, the workbench's client of the gh broker.

The script does its work as it loads, so each test runs it whole with
runpy, against a stand in broker listening on a unix socket in the test's
own directory.
"""

from __future__ import annotations

import errno
import io
import json
import os
import runpy
import socket
import threading

import pytest
from conftest import REPO_ROOT

GH = str(REPO_ROOT / "images/workbench/bin/gh")


class Broker:
    """Accepts one connection, records the request, answers `reply`."""

    def __init__(self, path, reply):
        self.reply = reply
        self.request = None
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(path))
        self.server.listen()
        self.thread = threading.Thread(target=self.answer, daemon=True)
        self.thread.start()

    def answer(self):
        conn, _ = self.server.accept()
        with conn:
            data = b""
            while chunk := conn.recv(65536):
                data += chunk
            self.request = json.loads(data)
            conn.sendall(self.reply)
        self.server.close()


class Stdin(io.StringIO):
    """A piped standard input."""


def run_gh(monkeypatch, argv, stdin=None):
    monkeypatch.setattr("sys.argv", ["gh", *argv])
    monkeypatch.setattr("sys.stdin", stdin or Stdin())
    with pytest.raises(SystemExit) as exit_:
        runpy.run_path(GH, run_name="gh")
    return exit_.value.code


@pytest.fixture
def sock(monkeypatch, tmp_path):
    path = tmp_path / "s"
    monkeypatch.setenv("GH_BROKER_SOCK", str(path))
    return path


def test_relays_the_reply(monkeypatch, capsys, sock, tmp_path):
    broker = Broker(sock, b'{"rc": 4, "out": "o", "err": "e"}')
    monkeypatch.chdir(tmp_path)
    assert run_gh(monkeypatch, ["pr", "list"], Stdin("piped")) == 4
    broker.thread.join()
    assert broker.request == {
        "argv": ["pr", "list"],
        "cwd": str(tmp_path),
        "stdin": "",
    }
    assert capsys.readouterr() == ("o", "e")


class Unread(Stdin):
    """A standard input that must not be read: one left open by whatever
    started gh would block it for ever."""

    def read(self, *args):
        raise AssertionError("stdin was read")


@pytest.mark.parametrize(
    "argv",
    [
        ["pr", "create", "--body-file", "-"],
        ["api", "graphql", "--input", "-"],
        ["pr", "create", "--body-file=-"],
        ["api", "graphql", "--input=-"],
        ["api", "graphql", "--raw-field=query=@-"],
        ["api", "graphql", "-F", "query=@-"],
        ["api", "graphql", "--field", "query=@-"],
        ["api", "graphql", "-f", "query=@-"],
        ["api", "graphql", "--raw-field", "query=@-"],
    ],
)
def test_stdin_is_sent_when_the_command_reads_it(monkeypatch, sock, argv):
    broker = Broker(sock, b'{"rc": 0, "out": "", "err": ""}')
    assert run_gh(monkeypatch, argv, Stdin("piped")) == 0
    broker.thread.join()
    assert broker.request["stdin"] == "piped"


@pytest.mark.parametrize(
    "argv",
    [
        ["pr", "list"],
        ["api", "graphql", "--input"],
        ["api", "graphql", "--input", "file.json"],
        ["api", "graphql", "-F", "query=@file"],
        ["api", "graphql", "-F"],
    ],
)
def test_stdin_is_left_alone_otherwise(monkeypatch, sock, argv):
    broker = Broker(sock, b'{"rc": 0, "out": "", "err": ""}')
    assert run_gh(monkeypatch, argv, Unread()) == 0
    broker.thread.join()
    assert broker.request["stdin"] == ""


class StalledSocket:
    """A connection that opens, then fails as `error` says when read."""

    error = TimeoutError

    def __init__(self, *args):
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, path):
        pass

    def sendall(self, data):
        pass

    def shutdown(self, how):
        pass

    def recv(self, size):
        raise self.error(32, "Broken pipe")


def test_a_broker_that_never_answers_times_out(monkeypatch, capsys, sock):
    monkeypatch.setattr("socket.socket", StalledSocket)
    assert run_gh(monkeypatch, ["pr", "list"]) == 124
    assert "no reply from the gh broker after 330s" in capsys.readouterr().err


def test_a_connection_that_breaks_is_unreachable(monkeypatch, capsys, sock):
    monkeypatch.setattr(StalledSocket, "error", BrokenPipeError)
    monkeypatch.setattr("socket.socket", StalledSocket)
    assert run_gh(monkeypatch, ["pr", "list"]) == 127
    assert "connection to the gh broker failed (Broken pipe)" in capsys.readouterr().err


def test_an_empty_reply_is_an_error(monkeypatch, capsys, sock):
    Broker(sock, b"")
    assert run_gh(monkeypatch, ["pr", "list"]) == 1
    assert "empty reply" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags",
    [
        ["--body-file", "{body}"],
        ["--body-file={body}"],
        ["-F", "{body}"],
        ["-F{body}"],
    ],
)
def test_a_body_file_is_read_here_and_sent_on_stdin(monkeypatch, sock, tmp_path, flags):
    body = tmp_path / "body.md"
    body.write_text("the body", encoding="utf-8")
    broker = Broker(sock, b'{"rc": 0, "out": "", "err": ""}')
    argv = ["pr", "create", *[f.format(body=body) for f in flags], "--draft"]
    assert run_gh(monkeypatch, argv, Stdin("ignored")) == 0
    broker.thread.join()
    assert broker.request["argv"] == ["pr", "create", "--body-file", "-", "--draft"]
    assert broker.request["stdin"] == "the body"


def test_a_body_on_stdin_passes_through(monkeypatch, sock):
    broker = Broker(sock, b'{"rc": 0, "out": "", "err": ""}')
    argv = ["pr", "create", "--body-file", "-", "--body-file"]
    assert run_gh(monkeypatch, argv, Stdin("from stdin")) == 0
    broker.thread.join()
    assert broker.request["argv"] == ["pr", "create", "--body-file", "-", "--body-file"]
    assert broker.request["stdin"] == "from stdin"


def test_an_unreadable_body_file_stops_here(monkeypatch, capsys, sock, tmp_path):
    missing = tmp_path / "absent.md"
    assert run_gh(monkeypatch, ["pr", "create", "--body-file", str(missing)]) == 1
    assert f"cannot read {missing}" in capsys.readouterr().err


def test_no_command_is_sent_as_is(monkeypatch, sock):
    broker = Broker(sock, b'{"rc": 0, "out": "", "err": ""}')
    assert run_gh(monkeypatch, []) == 0
    broker.thread.join()
    assert broker.request["argv"] == []


def test_an_unreachable_broker(monkeypatch, capsys, sock):
    assert run_gh(monkeypatch, ["pr", "list"]) == 127
    assert "not reachable" in capsys.readouterr().err


def blocked_socket(code):
    def make(*_args, **_kwargs):
        raise OSError(code, os.strerror(code))

    return make


def test_a_sandboxed_call_says_so(monkeypatch, capsys, sock):
    # Claude Code's command sandbox refuses to open any unix socket.
    monkeypatch.setattr(socket, "socket", blocked_socket(errno.EPERM))
    assert run_gh(monkeypatch, ["pr", "list"]) == 127
    err = capsys.readouterr().err
    assert "ran inside the agent's command sandbox" in err
    assert "host/workbench up" not in err


def test_a_socket_it_may_not_use_is_not_the_sandbox(monkeypatch, capsys, sock):
    monkeypatch.setattr(socket, "socket", blocked_socket(errno.EACCES))
    assert run_gh(monkeypatch, ["pr", "list"]) == 127
    err = capsys.readouterr().err
    assert "not reachable" in err
    assert "sandbox" not in err
