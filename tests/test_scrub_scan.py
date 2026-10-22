"""Tests for host/scrub-scan, the part of the secret scan that runs in a
container: what is live, tier 1 search, the decoded view gitleaks reads,
redaction that keeps JSON valid, and the ledger.

gitleaks itself is a stand in here (a script that applies the rules a test
gives it to the view, and writes gitleaks' report), so these run with no
image. host/scrub's own end to end run against the real gitleaks is in
docs/SCRUB.md, Testing. Every secret here is made up.
"""

# cspell:words AKIA lastindex NOEQUALS surrogateescape
from __future__ import annotations

import json
import os
import re
import sys

import pytest
from conftest import load

scrub = load("host/scrub-scan", "scrub_scan")

PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # pragma: allowlist secret
AWS = "AKIA" + "ABCDEFGHIJKLMN23"
API = "Zq8Kd93L" + "mx0Pw2Rt"
KNOWN = "known-secret-value-0123456789"
SHA = "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret
DIGEST = "sha256:" + "ab" * 32
UUID_A = "3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d41"
UUID_B = "11111111-2222-4333-8444-555555555555"

FAKE_GITLEAKS = """\
#!{python}
import json, os, re, sys
view, report = sys.argv[2], sys.argv[-1]
if os.environ.get("FAKE_RC", "0") != "0":
    print("boom", file=sys.stderr)
    sys.exit(int(os.environ["FAKE_RC"]))
rules = json.loads(os.environ.get("FAKE_RULES", "[]"))
findings = []
for name in sorted(os.listdir(view)):
    text = open(os.path.join(view, name), encoding="utf-8", errors="surrogateescape").read()
    for rule, pattern in rules:
        for m in re.finditer(pattern, text):
            findings.append({{"RuleID": rule, "File": os.path.join(view, name), "Secret": m.group(m.lastindex or 0)}})
if os.environ.get("FAKE_NULL"):
    findings = None
open(report, "w").write(json.dumps(findings))
"""

RULES = [
    ["github-pat", r"ghp_[A-Za-z0-9]{36}"],
    ["aws-access-token", r"AKIA[A-Z2-7]{16}"],
    ["generic-api-key", r"api_key: \"([A-Za-z0-9]{10,})\""],
    ["generic-high-entropy", r"\b(ZZ[A-Za-z0-9]{30,})\b"],
]


@pytest.fixture
def gitleaks(tmp_path, monkeypatch):
    path = tmp_path / "gitleaks"
    path.write_text(FAKE_GITLEAKS.format(python=sys.executable))
    path.chmod(0o755)
    monkeypatch.setenv("FAKE_RULES", json.dumps(RULES))
    return str(path)


class Work:
    """The folders host/scrub hands over: copies, manifest, context."""

    def __init__(self, root):
        self.work = root / "work"
        self.state = root / "state"
        self.home = str(root / "home")
        for d in ("files", "out"):
            (self.work / d).mkdir(parents=True)
        self.state.mkdir()
        self.n = 0
        self.manifest = []

    def add(self, rel, text, kind="host", mtime=0, home=None):
        self.n += 1
        n = str(self.n)
        (self.work / "files" / n).write_text(text)
        home = home or self.home
        self.manifest.append(
            f"{n}\t{len(text.encode())}\t{mtime}\t{kind}\t{home}\t{home}/{rel}"
        )
        (self.work / "manifest").write_text("".join(f"{m}\n" for m in self.manifest))
        return n

    def context(self, *lines):
        (self.work / "context").write_text("".join(f"{line}\n" for line in lines))

    def known(self, name, value):
        (self.work / "known").mkdir(exist_ok=True)
        (self.work / "known" / name).write_text(value + "\n")

    def env(self, text):
        (self.work / "known-env").mkdir(exist_ok=True)
        (self.work / "known-env" / "1").write_text(text)

    def run(self, gitleaks, *args, color=False):
        scrub.GITLEAKS = gitleaks
        return scrub.main([*(["--color"] if color else []), "scan", *args])

    def actions(self):
        return (self.work / "out" / "actions").read_text().split("\n")[:-1]

    def redacted(self, n):
        return (self.work / "out" / "redacted" / n).read_text()

    def ledger(self):
        return json.loads((self.state / "ledger.json").read_text())


@pytest.fixture
def w(tmp_path, monkeypatch):
    work = Work(tmp_path)
    work.context("now 100000", "quiet 600")
    # The scan's folders are fixed (where host/scrub mounts them): here,
    # the test's own.
    monkeypatch.setattr(scrub, "WORK", work.work)
    monkeypatch.setattr(scrub, "STATE", work.state)
    monkeypatch.setattr(scrub, "GITLEAKS", scrub.GITLEAKS)
    return work


def line(**fields):
    return json.dumps(fields, separators=(",", ":"))


def transcript(*texts):
    return "".join(line(type="user", message={"content": t}) + "\n" for t in texts)


# The acceptance fixture: a secret of each tier, and identifiers that are not.


def test_every_tier_is_redacted_and_identifiers_are_not(w, gitleaks, capsys):
    w.known("gh-broker-token", KNOWN)
    text = transcript(
        f"export GITHUB_TOKEN={PAT}",
        f'config: api_key: "{API}"\nand {AWS}',
        f"echo {KNOWN}",
        f"commit {SHA} image {DIGEST} id {UUID_A} run 18712345678",
    )
    n = w.add(f"projects/-w/{UUID_A}.jsonl", text)
    assert w.run(gitleaks, "--manual") == 0
    out = w.redacted(n)
    assert PAT not in out and AWS not in out and KNOWN not in out and API not in out
    assert f"[REDACTED:github-pat:...{PAT[-4:]}]" in out
    # 20 characters and more keep their last four.
    assert f"[REDACTED:aws-access-token:...{AWS[-4:]}]" in out
    assert f"[REDACTED:gh-broker-token:...{KNOWN[-4:]}]" in out
    assert "[REDACTED:generic-api-key]" in out  # under 20: no tail
    for keep in (SHA, DIGEST, UUID_A, "18712345678"):
        assert keep in out
    for row in out.splitlines():
        json.loads(row)
    said = [a for a in w.actions() if a.startswith("say 1 ")]
    assert len(said) == 4  # one line each, for host/scrub to say once replaced
    assert w.actions()[4:] == ["replace 1", f"done 1 {len(out.encode())}"]
    ledger = w.ledger()
    assert sorted(e["rule"] for e in ledger["entries"]) == [
        "aws-access-token",
        "generic-api-key",
        "gh-broker-token",
        "github-pat",
    ]
    assert all(
        len(e["hmac"]) == 64 and e["status"] == "open" for e in ledger["entries"]
    )
    printed = capsys.readouterr().out
    assert (
        f"say 1 scrub: github-pat ...{PAT[-4:]} in {w.home}/projects/-w/{UUID_A}.jsonl: redacted to [REDACTED:github-pat:...{PAT[-4:]}]. Rotate it"
        in "\n".join(said)
    )
    assert "redacted to" not in printed  # not before the file is replaced
    assert "is not rotated yet" in printed
    assert "1 file looked at" in printed
    assert KNOWN not in printed and PAT not in printed


def test_running_twice_changes_nothing_and_keeps_one_entry_each(w, gitleaks):
    w.add("history.jsonl", transcript(f"token {PAT}"))
    w.run(gitleaks)
    first = w.ledger()
    (w.work / "files" / "1").write_text(w.redacted("1"))
    w.run(gitleaks)
    assert w.actions() == [f"done 1 {w.manifest[0].split(chr(9))[1]}"]
    assert w.ledger()["entries"] == first["entries"]


def test_the_same_secret_in_two_files_is_one_entry(w, gitleaks):
    w.add("a.jsonl", transcript(PAT))
    w.add("b.jsonl", transcript(PAT))
    w.run(gitleaks)
    (entry,) = w.ledger()["entries"]
    assert len(entry["files"]) == 2


# What is live.


def entry(path="x/f.jsonl", kind="host", mtime=0, home="/h"):
    return scrub.Entry("1", 1, mtime, kind, home, f"{home}/{path}")


@pytest.mark.parametrize(
    ("e", "ctx", "live"),
    [
        (entry(), scrub.Context(open={"/h/x/f.jsonl"}, now=10**6), True),
        (entry(), scrub.Context(busy_all={"/h"}, now=10**6), True),
        (entry(f"p/{UUID_A}.jsonl"), scrub.Context(live={UUID_A}, now=10**6), True),
        (entry("history.jsonl"), scrub.Context(busy={"/h"}, now=10**6), True),
        (entry(f"p/{UUID_A}.jsonl"), scrub.Context(busy={"/h"}, now=10**6), False),
        (entry(mtime=10**6 - 5), scrub.Context(now=10**6), True),
        (
            entry(f"p/{UUID_A}.jsonl", mtime=10**6 - 5),
            scrub.Context(closed={UUID_A}, now=10**6),
            False,
        ),
        (entry(kind="workbench", mtime=10**6 - 5), scrub.Context(now=10**6), False),
        (entry(mtime=0), scrub.Context(now=10**6), False),
    ],
)
def test_is_live(e, ctx, live):
    assert scrub.is_live(e, ctx) is live


def test_a_live_file_is_reported_and_not_written(w, gitleaks, capsys):
    w.context("now 100000", f"live {UUID_B}")
    w.add(f"projects/-w/{UUID_B}.jsonl", transcript(PAT))
    w.add("quiet.jsonl", transcript("nothing here"))
    w.run(gitleaks)
    assert w.actions() == [f"done 2 {w.manifest[1].split(chr(9))[1]}"]
    assert not (w.work / "out" / "redacted" / "1").exists()
    assert (
        "a running session holds, so it waits for the session to end. Rotate it now"
        in capsys.readouterr().out
    )
    assert len(w.ledger()["entries"]) == 1


def test_check_reports_and_keeps_nothing(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT))
    assert w.run(gitleaks, "--check") == 0
    assert (
        f"would be redacted to [REDACTED:github-pat:...{PAT[-4:]}]"
        in capsys.readouterr().out
    )
    assert not (w.work / "out" / "actions").exists()
    assert not (w.state / "ledger.json").exists()
    assert not (w.state / "key").exists()


def test_a_dry_run_says_what_a_scan_would_do(w, gitleaks, monkeypatch, capsys):
    # One file of each outcome: redacted, waiting for its session, a hit not
    # written as found, a redaction that would break the JSON.
    monkeypatch.setenv(
        "FAKE_RULES", json.dumps([*RULES, ["odd", r"(t: SPLITVALUE12)"]])
    )
    w.context("now 100000", f"live {UUID_B}")
    w.known("cut", "n0123456789")
    w.add("a.jsonl", transcript(PAT))
    w.add(f"p/{UUID_B}.jsonl", transcript(PAT))
    w.add("b.jsonl", line(t="SPLITVALUE12") + "\n")
    w.add("c.jsonl", line(t="a\n0123456789") + "\n")
    before = {n: (w.work / "files" / n).read_text() for n in "1234"}
    assert w.run(gitleaks, "--check", "--manual") == 0
    printed = capsys.readouterr().out
    assert (
        f"a.jsonl: would be redacted to [REDACTED:github-pat:...{PAT[-4:]}]" in printed
    )
    assert "so it would wait for the session to end" in printed
    assert "not as it is written there" in printed
    assert "would break its JSON" in printed
    assert (
        "4 files looked at: 1 would be redacted, 1 wait for a session to end, "
        "2 cannot be redacted; dry run, nothing was changed"
    ) in printed
    assert not list((w.work / "out" / "redacted").iterdir())
    assert {n: (w.work / "files" / n).read_text() for n in "1234"} == before
    w.run(gitleaks, "--manual")
    assert "1 to redact" in capsys.readouterr().out


# The view gitleaks reads, and redaction.


def test_view_decodes_json_strings_with_their_keys():
    text = line(a={"api_key": 'x "q"\ny'}, b=[1, "s", None]) + "\nnot json\n"
    assert scrub.view("t.jsonl", text).split("\n") == [
        'api_key: x "q"',
        "y",
        "s",
        "not json",
    ]
    assert scrub.view("s.json", json.dumps({"k": "v"})) == "k: v"
    assert scrub.view("s.json", "{broken\n" + line(k="v")) == "{broken\nk: v"
    assert scrub.view("log.txt", "plain\ttext") == "plain\ttext"


def test_a_secret_with_quotes_is_found_in_its_escaped_form(w, gitleaks):
    w.known("pw", 'pa"ss\\word-0123456789')  # pragma: allowlist secret
    n = w.add("a.jsonl", line(t='run --pw pa"ss\\word-0123456789') + "\n")
    w.run(gitleaks)
    out = w.redacted(n)
    assert "pa" not in out.split("--pw")[1].split("[")[0]
    json.loads(out)


def test_plain_text_files_are_redacted_without_a_json_check(w, gitleaks):
    n = w.add("debug/x.txt", f"{{ broken {PAT}\n")
    w.run(gitleaks)
    assert w.redacted(n).startswith("{ broken [REDACTED:github-pat")


def test_redaction_that_would_break_json_is_refused(w, gitleaks, capsys):
    # Cut out of `\n0123456789`, it would leave a backslash before `[`.
    w.known("cut", "n0123456789")
    n = w.add("a.jsonl", line(t="a\n0123456789") + "\n")
    w.run(gitleaks)
    assert w.actions() == []
    assert not (w.work / "out" / "redacted" / n).exists()
    assert "would break its JSON" in capsys.readouterr().out


def test_a_whole_json_file_must_still_parse():
    assert scrub.still_valid("s.json", "{}", "{}")
    assert not scrub.still_valid("s.json", "{}", "{")
    assert scrub.still_valid("s.json", "{", "{x")
    assert not scrub.still_valid("t.jsonl", "{}\n{}", "{}")
    assert scrub.still_valid("t.jsonl", "bad\n{}", "worse\n{}")


def test_a_hit_not_written_as_found_is_reported_not_redacted(
    w, gitleaks, monkeypatch, capsys
):
    # The view writes a string beside its key (`t: value`); a rule matching
    # across both finds a value the file does not hold as it is.
    monkeypatch.setenv(
        "FAKE_RULES", json.dumps([*RULES, ["odd", r"(t: SPLITVALUE12)"]])
    )
    n = w.add("a.jsonl", line(t="SPLITVALUE12", u=PAT) + "\n")
    w.add("b.jsonl", line(t="SPLITVALUE12") + "\n")
    w.run(gitleaks)
    printed = capsys.readouterr().out
    assert "not as it is written there, so not redacted" in printed
    assert PAT not in w.redacted(n)
    assert "done 3" not in w.actions() and w.actions()[-1].startswith("done 2 ")


def test_report_only_hits_are_reported_on_manual_runs_only(w, gitleaks, capsys):
    loud = "ZZ" + "q7Rk2LmP9xW4vT8sN1bH6cJ3fD5gY0aE"  # pragma: allowlist secret
    w.add("a.jsonl", transcript(f"value {loud} end", f"also {PAT}"))
    w.run(gitleaks)
    assert "random looking" not in capsys.readouterr().out
    w.run(gitleaks, "--manual")
    printed = capsys.readouterr().out
    assert f"generic-high-entropy ...{loud[-4:]}" not in printed  # counted only
    assert "1 random looking string in 1 file, reported only" in printed
    assert "ENTROPY=1 (or --entropy) lists them" in printed
    w.run(gitleaks, "--manual", "--entropy")
    printed = capsys.readouterr().out
    assert f"generic-high-entropy ...{loud[-4:]}" in printed
    assert "ENTROPY=1 (or --entropy) lists them" not in printed
    assert all(e["rule"] != "generic-high-entropy" for e in w.ledger()["entries"])


def test_no_report_only_hits_no_count(w, gitleaks, capsys):
    w.add("a.jsonl", transcript("nothing random"))
    w.run(gitleaks, "--manual")
    assert "random looking" not in capsys.readouterr().out


def test_hits_of_skips_short_values_and_unknown_files_and_overlaps():
    e = scrub.Entry("1", 1, 0, "host", "/h", "/h/a")
    long, other, loud = (
        "a-long-enough-value",
        "another-value-here",
        "generic-high-entropy",
    )

    def finding(rule, file, value):
        return {"RuleID": rule, "File": file, "Secret": value}

    findings = [
        finding("r", "/v/1", "short"),
        finding("r", "/v/9", long),
        finding("r", "/v/1", long),
        finding(loud, "/v/1", long),
        finding(loud, "/v/1", other),
    ]
    redact, report = scrub.hits_of([e], {"1": ""}, {}, findings)
    assert redact == {"1": {long: ("r", None)}}
    assert report == {"1": {other}}


def test_tier_one_wins_over_a_rule_for_the_same_value(w, gitleaks):
    w.known("gh-broker-token", PAT)
    n = w.add("a.jsonl", transcript(PAT))
    w.run(gitleaks)
    assert f"[REDACTED:gh-broker-token:...{PAT[-4:]}]" in w.redacted(n)
    assert w.ledger()["entries"][0]["known"] == "gh-broker-token"


def test_tails():
    assert scrub.placeholder("r", "x" * 19) == "[REDACTED:r]"
    assert scrub.placeholder("r", "x" * 16 + "abcd") == "[REDACTED:r:...abcd]"
    assert scrub.placeholder("r", "x" * 16 + 'ab"d') == "[REDACTED:r]"
    assert scrub.shown("r", "x" * 16 + "abcd") == "r ...abcd"
    assert scrub.shown("r", "short") == "r"


# Tier 1 values.


def test_env_values():
    text = "# c\n\nNOEQUALS\nexport A=one\nB = 'two'\nC=\"three\"\nD=x\nE='\n"  # pragma: allowlist secret
    assert scrub.env_values(text) == {
        "A": "one",
        "B": "two",
        "C": "three",
        "D": "x",
        "E": "'",
    }


def test_read_known_keeps_long_values_only(w):
    assert scrub.read_known(w.work) == {}
    w.known("ssh-key", "-----BEGIN KEY-----\nabc\n-----END KEY-----")
    w.env("TOKEN=long-enough-value\nPIN=1234\n")  # pragma: allowlist secret
    assert scrub.read_known(w.work) == {
        "ssh-key": "-----BEGIN KEY-----\nabc\n-----END KEY-----",
        "env:TOKEN": "long-enough-value",
    }


def test_a_multi_line_key_is_found_escaped_in_json(w, gitleaks):
    key = "-----BEGIN KEY-----\nabc\n-----END KEY-----"
    w.known("ssh-key", key)
    n = w.add("a.jsonl", line(t=f"cat id\n{key}\n") + "\n")
    w.run(gitleaks)
    assert "BEGIN KEY" not in w.redacted(n)
    assert "[REDACTED:ssh-key:...----]" in w.redacted(n)


# The ledger.


def test_rotated_by_hand_then_found_somewhere_new_reopens(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT))
    w.run(gitleaks)
    assert scrub.main(["rotated", "1"]) == 0
    assert "secret #1 (github-pat) marked rotated" in capsys.readouterr().out
    assert w.ledger()["entries"][0]["status"] == "rotated"
    w.run(gitleaks)  # the same file again: still rotated
    assert w.ledger()["entries"][0]["status"] == "rotated"
    w.add("b.jsonl", transcript(PAT))
    w.run(gitleaks)
    (e,) = w.ledger()["entries"]
    assert e["status"] == "open" and "rotated" not in e
    assert "was marked rotated, and is found again" in capsys.readouterr().out


def test_rotated_needs_an_entry(w, capsys):
    assert scrub.main(["rotated", "7"]) == 2
    assert "no secret #7 in the ledger" in capsys.readouterr().out


def test_a_tier_one_secret_is_rotated_once_its_value_changes(w, gitleaks, capsys):
    w.known("gh-broker-token", KNOWN)
    w.env("OTHER=" + "another-known-value-99")
    w.add("a.jsonl", transcript(KNOWN, "another-known-value-99", PAT))
    w.run(gitleaks)
    printed = capsys.readouterr().out
    assert "marked rotated on its own once gh-broker-token changes" in printed
    w.known("gh-broker-token", "a-new-value-0000000000")
    (w.work / "known-env" / "1").unlink()
    w.add("b.jsonl", transcript("clean"))
    w.manifest = w.manifest[1:]
    (w.work / "manifest").write_text(w.manifest[0] + "\n")
    w.run(gitleaks)
    entries = {e["rule"]: e for e in w.ledger()["entries"]}
    assert entries["gh-broker-token"]["status"] == "rotated"
    assert entries["env:OTHER"]["status"] == "open"  # not readable now: unknown
    assert entries["github-pat"]["status"] == "open"
    assert re.search(
        r"secret #\d+ \(gh-broker-token\) has a new value now", capsys.readouterr().out
    )
    w.run(gitleaks)
    assert "has a new value" not in capsys.readouterr().out


def test_the_key_is_made_once_and_kept_private(w, gitleaks):
    w.add("a.jsonl", transcript(PAT))
    w.run(gitleaks)
    key = (w.state / "key").read_text()
    assert oct((w.state / "key").stat().st_mode & 0o777) == "0o600"
    assert oct((w.state / "ledger.json").stat().st_mode & 0o777) == "0o600"
    w.run(gitleaks)
    assert (w.state / "key").read_text() == key


def test_color_only_when_asked(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT))
    w.run(gitleaks, color=True)
    assert scrub.RED in capsys.readouterr().out
    w.run(gitleaks)
    assert scrub.RED not in capsys.readouterr().out


# Inputs and failures.


def test_no_files_still_lists_open_secrets(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT))
    w.run(gitleaks)
    (w.work / "manifest").write_text("\n")
    capsys.readouterr()
    assert w.run(gitleaks) == 0
    assert "secret #1 (github-pat" in capsys.readouterr().out
    assert w.actions() == []


def test_missing_inputs_are_empty(tmp_path):
    assert scrub.read_manifest(tmp_path / "none") == []
    ctx = scrub.read_context(tmp_path / "none")
    assert ctx.quiet == 600 and not ctx.busy


def test_an_unknown_context_line_fails(w, gitleaks, capsys):
    w.context("bogus x")
    assert w.run(gitleaks) == 1
    assert (
        "the scan failed, nothing was changed: unknown context line: bogus"
        in capsys.readouterr().out
    )


def test_context_lines(w):
    w.context(
        "busy /a", "busy-all /b", "live L", "closed C", "open /c d", "now 5", "quiet 60"
    )
    ctx = scrub.read_context(w.work / "context")
    assert (ctx.busy, ctx.busy_all, ctx.live, ctx.closed, ctx.open) == (
        {"/a"},
        {"/b"},
        {"L"},
        {"C"},
        {"/c d"},
    )
    assert (ctx.now, ctx.quiet) == (5, 60)


def test_gitleaks_failing_fails_the_scan(w, gitleaks, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_RC", "3")
    w.add("a.jsonl", transcript(PAT))
    assert w.run(gitleaks) == 1
    assert "gitleaks failed: boom" in capsys.readouterr().out
    assert not (w.work / "out" / "actions").exists()


def test_gitleaks_reporting_null_is_no_findings(w, gitleaks, monkeypatch):
    monkeypatch.setenv("FAKE_NULL", "1")
    w.add("a.jsonl", transcript("clean"))
    assert w.run(gitleaks) == 0
    assert w.actions()[0].startswith("done 1")


def test_a_ledger_on_disk_is_read(w, gitleaks):
    (w.state / "key").write_text("00" * 32)
    (w.state / "ledger.json").write_text(json.dumps({"next": 5, "entries": []}))
    w.add("a.jsonl", transcript(PAT))
    w.run(gitleaks)
    assert w.ledger()["entries"][0]["id"] == 5


def test_main_guard_runs_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scrub-scan"])
    with pytest.raises(SystemExit) as exit_:
        load("host/scrub-scan", "__main__")
    assert exit_.value.code == 2  # no command


def test_its_folders_are_fixed_not_given():
    assert (str(scrub.WORK), str(scrub.STATE)) == ("/work", "/state")
    assert os.path.basename(scrub.GITLEAKS) == "gitleaks"
    with pytest.raises(SystemExit):
        scrub.main(["scan", "--work", "/elsewhere"])
