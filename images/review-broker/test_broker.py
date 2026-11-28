"""Refuse and withhold cases for the review broker, run when its image is
built, so a change that reopens one fails the build. The reviewer is a stub
here: nothing reaches the network."""
import base64
import io
import json
import os
import stat
import sys
import tarfile
import tempfile

work = tempfile.mkdtemp()
login_dir = os.path.join(work, "login")
os.makedirs(login_dir)
TOKEN = "tok-" + "A1b2C3d4" * 8
REFRESHED = "tok-" + "Z9y8X7w6" * 8
with open(os.path.join(login_dir, "auth.json"), "w") as f:
    json.dump({"tokens": {"access_token": TOKEN, "account_id": "short"}}, f)

# The stub reviewer: records its command line, environment and standard
# input, writes an answer, and does what the file `do` asks (the broker
# passes it no environment of ours).
stub = os.path.join(work, "codex")
with open(stub, "w") as f:
    f.write(f"""#!{sys.executable}
import json, os, sys
argv = sys.argv[1:]
rec = {{"argv": argv, "stdin": sys.stdin.read(), "home": os.environ["CODEX_HOME"],
        "home_files": sorted(os.listdir(os.environ["CODEX_HOME"])), "cwd": os.getcwd(),
        "tree_files": sorted(os.listdir("."))}}
json.dump(rec, open({os.path.join(work, "record.json")!r}, "w"))
open({os.path.join(work, "config.toml")!r}, "w").write(open(os.path.join(os.environ["CODEX_HOME"], "config.toml")).read())
answer = argv[argv.index("--output-last-message") + 1]
do = open({os.path.join(work, "do")!r}).read() if os.path.exists({os.path.join(work, "do")!r}) else ""
text = "No problems found.\\n\\nVERDICT: CLEAN\\n"
if do == "leak":
    text = "Here it is: " + json.load(open(os.path.join(os.environ["CODEX_HOME"], "auth.json")))["tokens"]["access_token"][5:40]
if do == "refresh":
    json.dump({{"tokens": {{"access_token": {REFRESHED!r}}}}}, open(os.path.join(os.environ["CODEX_HOME"], "auth.json"), "w"))
if do == "fail":
    sys.stderr.write("stub: boom\\n")
    sys.exit(3)
open(answer, "w").write(text)
""")
os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)

os.environ.update(REVIEW_LOGIN_DIR=login_dir, REVIEW_CODEX=stub, REVIEW_PER_HOUR="5",
                  REVIEW_MODELS="gpt-x", REVIEW_MAX_BYTES="100000")
src = open("/usr/local/libexec/review-broker/broker.py").read()
ns = {"__name__": "broker"}
exec(compile(src, "broker.py", "exec"), ns)
handle = ns["handle"]
failures = []


def stub_do(what):
    with open(os.path.join(work, "do"), "w") as f:
        f.write(what)


def expect(name, cond):
    if not cond:
        failures.append(name)
    print(("ok " if cond else "FAIL ") + name)


def tree(files=None, extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, body in (files or {"README.md": b"hello\n", "src/a.py": b"x = 1\n"}).items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        for info in extra or []:
            tar.addfile(info)
    return base64.b64encode(buf.getvalue()).decode()


def req(**kw):
    r = {"reviewer": "codex", "model": "", "effort": "medium", "prompt": "Review this.\n+diff\n", "tree": tree()}
    r.update(kw)
    return r


def record():
    return json.load(open(os.path.join(work, "record.json")))


# A clean review: the answer comes back, and the command line is the blind one.
r = handle(req(), 1)
expect("a review returns the answer", r["rc"] == 0 and r["out"].rstrip().endswith("VERDICT: CLEAN"))
rec = record()
argv = rec["argv"]
expect("no --sandbox to stand in for the profile", "--sandbox" not in argv)
config = open(os.path.join(work, "config.toml")).read()
expect("the permissions profile is the default", 'default_permissions = "review"' in config)
expect("its shell reads the tree, not Codex's home", f'"{rec["cwd"]}" = "read"' in config and ".codex" not in config)
expect("ephemeral", "--ephemeral" in argv)
expect("no project docs", "project_doc_max_bytes=0" in argv)
expect("no developer instructions", 'developer_instructions=""' in argv)
expect("no skills", "skills.include_instructions=false" in argv)
expect("no web search", 'web_search="disabled"' in argv)
expect("memories off", "memories" in argv and argv[argv.index("memories") - 1] == "--disable")
expect("the effort asked for", 'model_reasoning_effort="medium"' in argv)
expect("the prompt on standard input", rec["stdin"] == "Review this.\n+diff\n")
expect("a fresh Codex home holding only the login and the profile", rec["home_files"] == ["auth.json", "config.toml"])
expect("the reviewer runs in the tree", rec["tree_files"] == ["README.md", "src"])
expect("nothing left behind", not os.path.exists(rec["cwd"]))

# What a request may choose.
expect("an allowed model", handle(req(model="gpt-x"), 2)["rc"] == 0 and 'model="gpt-x"' in record()["argv"])
for name, bad in [("another reviewer", {"reviewer": "claude"}), ("a model not allowed", {"model": "gpt-other"}),
                  ("an unknown effort", {"effort": "max"}), ("no prompt", {"prompt": " "})]:
    expect(f"refused: {name}", handle(req(**bad), 3)["rc"] == 126)

# What a tree may hold.
link = tarfile.TarInfo("evil")
link.type = tarfile.SYMTYPE
link.linkname = "/etc/passwd"
dev = tarfile.TarInfo("dev")
dev.type = tarfile.CHRTYPE
for name, t in [("a link", tree(extra=[link])), ("a device", tree(extra=[dev])),
                ("an absolute path", tree({"/etc/x": b"x"})), ("a path outside", tree({"../x": b"x"})),
                ("a .codex config", tree({".codex/config.toml": b"model_instructions_file = 'x'\n"})),
                ("a nested .codex", tree({"sub/.codex/config.toml": b"x"})),
                ("a tree over the cap", tree({"big": b"0" * 200000}))]:
    expect(f"refused: {name}", handle(req(tree=t), 4)["rc"] == 126)
expect("malformed base64", handle(req(tree="!!!"), 5)["rc"] == 2)

# The login stays in the broker.
stub_do("leak")
r = handle(req(), 6)
expect("a reply holding part of the login is withheld", r["rc"] == 126 and TOKEN[5:40] not in r["out"] + r["err"])
stub_do("refresh")
handle(req(), 7)
expect("a refreshed login is saved", json.load(open(os.path.join(login_dir, "auth.json")))["tokens"]["access_token"] == REFRESHED)
stub_do("fail")
r = handle(req(), 8)
expect("the reviewer's failure comes back", r["rc"] == 3 and "boom" in r["err"])
stub_do("")

# The hourly cap: 5 allowed, and only the reviews that ran counted.
r = handle(req(), 9)
expect("over the hourly cap", r["rc"] == 126 and "REVIEW_PER_HOUR" in r["err"])

# No login, no review.
os.remove(os.path.join(login_dir, "auth.json"))
ns["_started"].clear()
expect("no login", handle(req(), 10)["rc"] == 125)

if failures:
    print(f"{len(failures)} failed: {', '.join(failures)}", file=sys.stderr)
    sys.exit(1)
