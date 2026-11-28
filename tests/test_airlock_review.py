"""Tests for images/l2/bin/airlock-review, the L2 client of the review
broker. A real git repository in the test's directory, and a fake broker on
a socket there."""

from __future__ import annotations

import base64
import io
import json
import socket
import subprocess
import sys
import tarfile
import threading

import pytest
from conftest import load


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    return load("images/l2/bin/airlock-review", "airlock_review")


# What HEAD holds, as git would list it: regular files (one executable), a
# symbolic link and a submodule, which the tree leaves out. The Python image
# the tests run in has no git, so git is faked at the subprocess calls.
BLOBS = {"a1": b"alpha\n", "b2": b"\x00binary\n", "c3": b"echo hi\n", "d4": b"a.txt"}
LISTING = (
    b"100644 blob a1\ta.txt\0"
    b"100755 blob c3\trun.sh\0"
    b"100644 blob b2\tsub/b.txt\0"
    b"120000 blob d4\tlink\0"
    b"160000 commit e5\tvendored\0"
)


class CatFile:
    """`git cat-file --batch`: a header and the body for each name asked."""

    def __init__(self, argv, stdin, stdout):
        assert argv == ["git", "cat-file", "--batch"]
        self.asked = []
        self.stdin = self
        self.stdout = io.BytesIO()

    def write(self, data):
        sha = data.strip().decode()
        body = BLOBS[sha]
        pos = self.stdout.tell()
        self.stdout.seek(0, 2)
        self.stdout.write(f"{sha} blob {len(body)}\n".encode() + body + b"\n")
        self.stdout.seek(pos)

    def flush(self):
        pass

    def close(self):
        pass

    def wait(self):
        return 0


@pytest.fixture
def repo(client, monkeypatch):
    def run(argv, **kw):
        assert argv == ["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"]
        assert kw == {"capture_output": True, "check": True}
        return subprocess.CompletedProcess(argv, 0, LISTING, b"")

    monkeypatch.setattr(client.subprocess, "run", run)
    monkeypatch.setattr(client.subprocess, "Popen", CatFile)


def members(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {m.name: (m.mode, tar.extractfile(m).read()) for m in tar.getmembers()}


def test_the_tree_is_head_regular_files_only(client, repo):
    got = members(client.head_tree())
    assert got == {
        "a.txt": (0o644, b"alpha\n"),
        "run.sh": (0o755, b"echo hi\n"),
        "sub/b.txt": (0o644, b"\x00binary\n"),
    }


@pytest.mark.parametrize(
    "error", [OSError(2, "No such file"), subprocess.CalledProcessError(128, "git")]
)
def test_outside_a_checkout(client, monkeypatch, capsys, error):
    def run(*_a, **_k):
        raise error

    monkeypatch.setattr(client.subprocess, "run", run)
    with pytest.raises(SystemExit) as e:
        client.head_tree()
    assert e.value.code == 2
    assert "run it in a git checkout" in capsys.readouterr().err


class Broker:
    """A broker on a socket that records the request and sends `reply`."""

    def __init__(self, path, reply):
        self.req = None
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(str(path))
        self.s.listen()
        self.t = threading.Thread(target=self.serve, args=(reply,), daemon=True)
        self.t.start()

    def serve(self, reply):
        conn, _ = self.s.accept()
        with conn:
            self.req = json.loads(conn.makefile("rb").readline())
            conn.sendall(reply)


def run_main(client, monkeypatch, argv, stdin):
    monkeypatch.setattr(sys, "argv", ["airlock-review", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as e:
        client.main()
    return e.value.code


def test_main_sends_the_prompt_and_tree_and_prints_the_answer(
    client, repo, tmp_path, monkeypatch, capsys
):
    sock = tmp_path / "review.sock"
    b = Broker(
        sock,
        json.dumps(
            {"rc": 3, "out": "answer\nVERDICT: FINDINGS\n", "err": "note\n"}
        ).encode(),
    )
    monkeypatch.setenv("REVIEW_BROKER_SOCKET", str(sock))
    code = run_main(
        client, monkeypatch, ["--model", "gpt-x", "--effort", "high"], "the prompt\n"
    )
    b.t.join(5)
    assert code == 3
    out = capsys.readouterr()
    assert out.out == "answer\nVERDICT: FINDINGS\n" and out.err == "note\n"
    assert {k: b.req[k] for k in ("reviewer", "model", "effort", "prompt")} == {
        "reviewer": "codex",
        "model": "gpt-x",
        "effort": "high",
        "prompt": "the prompt\n",
    }
    assert "a.txt" in members(base64.b64decode(b.req["tree"]))


def test_main_with_a_reply_that_is_not_json(
    client, repo, tmp_path, monkeypatch, capsys
):
    sock = tmp_path / "review.sock"
    Broker(sock, b"<html>")
    monkeypatch.setenv("REVIEW_BROKER_SOCKET", str(sock))
    assert run_main(client, monkeypatch, [], "p") == 1
    assert "not JSON" in capsys.readouterr().err


def test_main_with_an_empty_reply(client, repo, tmp_path, monkeypatch, capsys):
    sock = tmp_path / "review.sock"
    Broker(sock, b"")
    monkeypatch.setenv("REVIEW_BROKER_SOCKET", str(sock))
    assert run_main(client, monkeypatch, [], "p") == 1
    assert "empty reply" in capsys.readouterr().err


@pytest.mark.parametrize("value", [None, "", "/nonexistent/review.sock"])
def test_main_without_a_broker(client, repo, monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv("REVIEW_BROKER_SOCKET", raising=False)
    else:
        monkeypatch.setenv("REVIEW_BROKER_SOCKET", value)
    assert run_main(client, monkeypatch, [], "p") == 127
    assert "l2 --review" in capsys.readouterr().err


def test_main_when_the_socket_refuses(client, repo, tmp_path, monkeypatch, capsys):
    dead = tmp_path / "dead.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(dead))
    s.close()
    monkeypatch.setenv("REVIEW_BROKER_SOCKET", str(dead))
    assert run_main(client, monkeypatch, [], "p") == 127
    assert "is not reachable" in capsys.readouterr().err


def test_main_when_no_reply_comes(client, repo, tmp_path, monkeypatch, capsys):
    sock = tmp_path / "slow.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock))
    s.listen()
    monkeypatch.setattr(client, "TIMEOUT", 0.2)
    monkeypatch.setenv("REVIEW_BROKER_SOCKET", str(sock))
    assert run_main(client, monkeypatch, [], "p") == 124
    assert "no reply from the review broker" in capsys.readouterr().err
    s.close()
