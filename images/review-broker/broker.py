"""review broker: run the blind cross review for a workbench, holding the
reviewer's login.

Listens on REVIEW_BROKER_DIR/review.sock. Each connection carries one JSON
line:

  {"reviewer": "codex", "model": "", "effort": "medium",
    "prompt": "<the review prompt and the diff>",
    "tree": "<base64 of a tar of the tree under review>"}

and gets back one JSON object, {"rc": int, "out": str, "err": str}: out is
the reviewer's answer. Every request is logged to stdout (size, reviewer,
model, effort, then status, duration and the answer's last line), so
`podman logs review-broker` is the record of what was reviewed.

The request chooses the reviewer, the model (from REVIEW_MODELS) and the
effort, nothing else: the command line is this file's. Each review runs in a
folder of its own under /tmp, removed afterwards, holding the tree and a
fresh CODEX_HOME with nothing in it but a copy of the login. So no
configuration, instruction file, skill, MCP server or memory reaches the
reviewer, and nothing it leaves behind reaches the next review. When Codex
refreshes its login during a review, the new one is copied back to the login
folder.

Codex's read only sandbox stops writes and the network, not reads, and the
login is a file Codex reads. So a reply that holds any part of it (any 24
characters of any of its values) is withheld: a diff carrying instructions
could ask the reviewer to print it.

Refused: a reviewer other than codex, a model or effort not allowed, a tree
over REVIEW_MAX_BYTES or with an unsafe member (a link, an absolute path, a
path outside the tree), a tree holding a .codex path (Codex project
configuration), and more than REVIEW_PER_HOUR reviews in an hour. One review
runs at a time; a request waits for the one before it, up to REVIEW_TIMEOUT.

Exit status codes in the reply:
  2    malformed request
  124  the review, or the wait for the one before it, took longer than
        REVIEW_TIMEOUT seconds
  125  the broker cannot review: no login, or the tree could not be unpacked
  126  refused (the message says why), or the reply was withheld
  any  otherwise, the exit status of the reviewer itself
"""
import base64
import collections
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

SOCK_DIR = os.environ.get("REVIEW_BROKER_DIR", "/run/review-broker")
LOGIN_DIR = os.environ.get("REVIEW_LOGIN_DIR", "/var/lib/review-broker/codex")
CODEX = os.environ.get("REVIEW_CODEX", "/usr/local/bin/codex")
PER_HOUR = int(os.environ.get("REVIEW_PER_HOUR", "10"))
TIMEOUT = int(os.environ.get("REVIEW_TIMEOUT", "900"))
MAX_BYTES = int(os.environ.get("REVIEW_MAX_BYTES", str(50 * 1024 * 1024)))
# An empty model means the default of Codex itself.
MODELS = frozenset({""} | {m.strip() for m in os.environ.get("REVIEW_MODELS", "").split(",") if m.strip()})
EFFORTS = frozenset({"low", "medium", "high"})
REVIEWERS = frozenset({"codex"})
# Base64 grows the tree by a third; the prompt carries the diff.
MAX_LINE = MAX_BYTES * 4 // 3 + 16 * 1024 * 1024
# How much of a login value a reply may not hold.
WINDOW = 24
# The Codex features that reach the network or other programs, or carry
# memory. Its shell stays, to read the code, inside the read only sandbox.
DISABLED = (
    "memories", "external_agent_memory_import", "apps", "plugins",
    "remote_plugin", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "image_generation",
    "skill_mcp_dependency_install", "in_app_local_automation", "in_app_browser",
)

_one_at_a_time = threading.Lock()
_started = collections.deque()
_started_lock = threading.Lock()


def log(*words):
    print(*words, flush=True)


def check(req):
    """Why the request is refused, or None."""
    if req.get("reviewer") not in REVIEWERS:
        return f"reviewer {req.get('reviewer')!r} is not served here (only: {', '.join(sorted(REVIEWERS))})"
    if req.get("model", "") not in MODELS:
        return f"model {req.get('model')!r} is not allowed (REVIEW_MODELS: {', '.join(sorted(m for m in MODELS if m)) or 'none'}; empty for the default)"
    if req.get("effort") not in EFFORTS:
        return f"effort {req.get('effort')!r} is not one of {', '.join(sorted(EFFORTS))}"
    if not isinstance(req.get("prompt"), str) or not req["prompt"].strip():
        return "no prompt"
    if not isinstance(req.get("tree"), str):
        return "no tree"
    return None


def unpack(data, dest):
    """Unpack the tar in `data` into `dest`. Only plain files and folders
    with relative paths that stay inside; anything else is a refusal, so the
    reviewer reads exactly the tree that was sent."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        members = tar.getmembers()
        for m in members:
            parts = m.name.split("/")
            if m.name.startswith("/") or ".." in parts:
                raise ValueError(f"unsafe path in the tree: {m.name}")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"not a file or folder in the tree: {m.name}")
            if ".codex" in parts:
                raise ValueError(f"the tree holds {m.name}: Codex project configuration is refused")
        tar.extractall(dest, members=members, filter="data")


def login_values(path):
    """Every string in the login file long enough to be a credential."""
    out = []

    def walk(v):
        if isinstance(v, str) and len(v) >= WINDOW:
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    with open(path, encoding="utf-8") as f:
        walk(json.load(f))
    return out


def leaks(text, secrets):
    """Whether `text` holds any WINDOW characters of any secret."""
    windows = {s[i:i + WINDOW] for s in secrets for i in range(len(s) - WINDOW + 1)}
    return any(text[i:i + WINDOW] in windows for i in range(len(text) - WINDOW + 1))


def over_rate():
    """Count this review against the hourly cap; True if it is over."""
    now = time.monotonic()
    with _started_lock:
        while _started and now - _started[0] > 3600:
            _started.popleft()
        if len(_started) >= PER_HOUR:
            return True
        _started.append(now)
        return False


def codex_command(req, tree, answer):
    cmd = [
        CODEX, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
        "--cd", tree, "--output-last-message", answer,
        "-c", f'model_reasoning_effort="{req["effort"]}"',
        "-c", "project_doc_max_bytes=0",
        "-c", 'developer_instructions=""',
        "-c", "skills.include_instructions=false",
        "-c", 'web_search="disabled"',
    ]
    for feature in DISABLED:
        cmd += ["--disable", feature]
    if req.get("model"):
        cmd += ["-c", f'model="{req["model"]}"']
    return cmd + ["-"]


def save_login(src, login):
    """Copy a login Codex refreshed back to the login folder, atomically."""
    with open(src, "rb") as f:
        new = f.read()
    with open(login, "rb") as f:
        if f.read() == new:
            return
    fd, tmp = tempfile.mkstemp(dir=LOGIN_DIR, prefix=".auth.")
    with os.fdopen(fd, "wb") as f:
        f.write(new)
    os.chmod(tmp, 0o600)
    os.replace(tmp, login)
    log("LOGIN refreshed")


def review(req, rid):
    """Run one review. The reply, as a dict."""
    login = os.path.join(LOGIN_DIR, "auth.json")
    if not os.path.isfile(login):
        return {"rc": 125, "out": "", "err": "review-broker: no Codex login; on the host: host/workbench review-login\n"}
    try:
        data = base64.b64decode(req["tree"], validate=True)
    except ValueError:
        return {"rc": 2, "out": "", "err": "review-broker: the tree is not base64\n"}
    if len(data) > MAX_BYTES:
        return {"rc": 126, "out": "", "err": f"review-broker: refused: the tree is {len(data)} bytes, over REVIEW_MAX_BYTES ({MAX_BYTES})\n"}
    work = tempfile.mkdtemp(prefix=f"review-{rid}-")
    try:
        tree = os.path.join(work, "tree")
        home = os.path.join(work, "home")
        codex_home = os.path.join(home, ".codex")
        os.makedirs(tree)
        os.makedirs(codex_home, mode=0o700)
        try:
            unpack(data, tree)
        except (ValueError, tarfile.TarError) as e:
            return {"rc": 126, "out": "", "err": f"review-broker: refused: {e}\n"}
        # Counted here, once the request is one that would really run.
        if over_rate():
            log("REFUSE", rid, "hourly cap")
            return {"rc": 126, "out": "", "err": f"review-broker: refused: {PER_HOUR} reviews in the last hour (REVIEW_PER_HOUR)\n"}
        shutil.copy2(login, os.path.join(codex_home, "auth.json"))
        answer = os.path.join(work, "answer.md")
        keep = ("PATH", "LANG", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy", "SSL_CERT_FILE")
        env = {k: v for k, v in os.environ.items() if k in keep}
        env.update(HOME=home, CODEX_HOME=codex_home, TMPDIR=work)
        try:
            p = subprocess.run(
                codex_command(req, tree, answer), input=req["prompt"], env=env,
                cwd=tree, capture_output=True, text=True, timeout=TIMEOUT,
            )
            rc, err = p.returncode, p.stderr
        except subprocess.TimeoutExpired:
            rc, err = 124, f"review-broker: the review took longer than {TIMEOUT}s\n"
        try:
            save_login(os.path.join(codex_home, "auth.json"), login)
        except OSError as e:
            log("LOGIN not saved:", e)
        out = ""
        if os.path.isfile(answer):
            with open(answer, encoding="utf-8", errors="replace") as f:
                out = f.read()
        secrets = login_values(login) + login_values(os.path.join(codex_home, "auth.json"))
        if leaks(out, secrets) or leaks(err, secrets):
            return {"rc": 126, "out": "", "err": "review-broker: the reply held part of the reviewer's login and was withheld\n"}
        # Codex's own log on stderr is long; its tail says what went wrong.
        return {"rc": rc, "out": out, "err": err[-4000:] if rc else ""}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def last_line(text):
    lines = [re.sub(r"[*`_]", "", x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]
    return lines[-1][:80] if lines else "-"


def handle(req, rid):
    why = check(req)
    if why is not None:
        log("REFUSE", rid, why)
        return {"rc": 126, "out": "", "err": f"review-broker: refused: {why}\n"}
    log("REVIEW", rid, req["reviewer"], req.get("model") or "default", req["effort"],
        f"prompt={len(req['prompt'])}", f"tree={len(req['tree']) * 3 // 4}")
    if not _one_at_a_time.acquire(timeout=TIMEOUT):
        log("BUSY", rid)
        return {"rc": 124, "out": "", "err": f"review-broker: the review before this one still runs after {TIMEOUT}s\n"}
    try:
        start = time.monotonic()
        reply = review(req, rid)
        log("DONE", rid, f"rc={reply['rc']}", f"{time.monotonic() - start:.0f}s", last_line(reply["out"]))
        return reply
    finally:
        _one_at_a_time.release()


def serve(conn, rid):
    with conn:
        try:
            line = conn.makefile("rb").readline(MAX_LINE + 1)
            if len(line) > MAX_LINE:
                conn.sendall(json.dumps({"rc": 126, "out": "", "err": "review-broker: refused: request too large\n"}).encode())
                return
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            conn.sendall(b'{"rc":2,"out":"","err":"review-broker: malformed request\\n"}')
            return
        reply = handle(req, rid)
        try:
            conn.sendall(json.dumps(reply).encode())
        except OSError:
            log("GONE", rid)


def main():
    if not os.path.isdir(SOCK_DIR):
        print(f"review-broker: no {SOCK_DIR} to listen in; the host mounts it", file=sys.stderr)
        sys.exit(1)
    path = os.path.join(SOCK_DIR, "review.sock")
    if os.path.exists(path):
        os.unlink(path)
    s = socket.socket(socket.AF_UNIX)
    s.bind(path)
    os.chmod(path, 0o660)
    s.listen()
    have = "a login" if os.path.isfile(os.path.join(LOGIN_DIR, "auth.json")) else "no login yet (host/workbench review-login)"
    log(f"review-broker: listening on {path}, {have}, models: {', '.join(sorted(m or 'default' for m in MODELS))},"
        f" {PER_HOUR} an hour, {TIMEOUT}s each")
    n = 0
    while True:
        conn, _ = s.accept()
        n += 1
        threading.Thread(target=serve, args=(conn, n), daemon=True).start()


if __name__ == "__main__":
    main()
