"""Tests for images/workbench/bin/gh, the workbench's client of the gh broker.

The script does its work as it loads, so each test runs it whole with
runpy, against a stand in broker listening on a unix socket in the test's
own directory.
"""

from __future__ import annotations

import io
import json
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
    def __init__(self, text="", tty=False):
        super().__init__(text)
        self.tty = tty

    def isatty(self):
        return self.tty


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
        "stdin": "piped",
    }
    assert capsys.readouterr() == ("o", "e")


def test_a_terminal_sends_no_stdin(monkeypatch, sock):
    broker = Broker(sock, b'{"rc": 0, "out": "", "err": ""}')
    assert run_gh(monkeypatch, ["api", "user"], Stdin("typed", tty=True)) == 0
    broker.thread.join()
    assert broker.request["stdin"] == ""


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
