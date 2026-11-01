"""Tests for host/scrub-scan, the part of the secret scan that runs in a
container: what is live, tier 1 search, the decoded view gitleaks reads,
redaction that keeps JSON valid, and the ledger.

gitleaks itself is a stand in here (a script that applies the rules a test
gives it to the view, and writes gitleaks' report), so these run with no
image. host/scrub's own end to end run against the real gitleaks is in
docs/SCRUB.md, Testing. Every secret here is made up.
"""

# cspell:words AKIA abcdéfgh fixt fixturenvalue fixturevalue lastindex changeme Jname xxxxab maa mtoken MIIE NOEQUALS nistp Rdescudv saltsalt WRWJT surrogateescape
from __future__ import annotations

import base64
import json
import os
import re
import sys
import tomllib

import pytest
from conftest import REPO_ROOT, load

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
    said = [a for a in w.actions() if a.startswith("hit 1 ")]
    assert len(said) == 4  # one each, for host/scrub to say once replaced
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
    assert re.search(
        rf"hit 1 secret #\d \(github-pat \.\.\.{PAT[-4:]}\)", "\n".join(said)
    )
    assert "redacted" not in printed.split("looked at")[0]  # host/scrub says it
    assert "make scrub-rotated ID=<n>" in printed
    assert "1 file looked at, 4 secrets in them: 1 to redact" in printed
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
    # A session start or end counts them; a manual run lists each.
    printed = capsys.readouterr().out
    assert "1 secret in files a running session holds, redacted once it ends" in printed
    assert "waits for a session" not in printed
    w.run(gitleaks, "--manual")
    assert (
        f"waits for a session to end in {w.home}/projects/-w/{UUID_B}.jsonl. Rotate it"
        in capsys.readouterr().out
    )
    assert len(w.ledger()["entries"]) == 1


def test_check_reports_and_keeps_nothing(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT))
    assert w.run(gitleaks, "--check") == 0
    assert (
        f"github-pat ...{PAT[-4:]}: would be redacted in {w.home}/a.jsonl. Rotate it"
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
    # The same token in two files is one line, with what happens in each.
    assert (
        f"github-pat ...{PAT[-4:]}: would be redacted in {w.home}/a.jsonl; "
        f"would wait for a session to end in {w.home}/p/{UUID_B}.jsonl. Rotate it"
    ) in printed
    assert f"odd: cannot be redacted in {w.home}/b.jsonl" in printed
    assert f"cut: cannot be redacted in {w.home}/c.jsonl" in printed
    assert "would break its JSON" in printed
    assert (
        "4 files looked at, 3 secrets in them: 1 would be redacted, 1 wait for a "
        "session to end, 2 keep something that cannot be redacted; dry run, nothing was changed"
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


def test_a_whole_json_file_that_would_break_is_refused(w, gitleaks, capsys):
    # A JSON file is one document, with no lines to leave apart.
    w.known("cut", "n0123456789")
    n = w.add("a.json", json.dumps({"t": "a\n0123456789", "u": f"token {PAT}"}))
    w.run(gitleaks, "--manual")
    assert w.actions() == []
    assert not (w.work / "out" / "redacted" / n).exists()
    printed = capsys.readouterr().out
    assert (
        "a.json: redacting it would break its JSON, so it is left as it is" in printed
    )
    assert re.search(
        r"\(github-pat \.\.\.Q7r8\): cannot be redacted in .*a\.json", printed
    )


def test_a_line_that_would_break_leaves_only_itself(w, gitleaks, capsys):
    w.known("cut", "n0123456789")
    first = line(t="a\n0123456789")
    n = w.add("a.jsonl", first + "\n" + line(t=f"token {PAT}") + "\n")
    w.run(gitleaks, "--manual")
    printed = capsys.readouterr().out
    out = w.redacted(n)
    # The other line is redacted; the one that would break is as it was.
    assert out.split("\n")[0] == first and PAT not in out
    assert all(scrub.parses(row) for row in out.splitlines())
    assert (
        "a.jsonl: redacting 1 line of it would break its JSON, so those are left as they are"
        in printed
    )
    assert re.search(
        r"secret #\d+ \(cut\): cannot be redacted in .*a\.jsonl \(line 1\)\. Rotate it",
        printed,
    )
    assert (
        "1 to redact, 0 wait for a session to end, 1 keep something that cannot be redacted"
        in printed
    )
    assert "replace 1" in w.actions()


@pytest.mark.parametrize("name", ["a.jsonl", "a.json"])
def test_in_json_a_value_with_a_backslash_is_replaced_as_escaped(w, gitleaks, name):
    # Its escaped spelling is the value in "a"; as it is, it would also match
    # the first half of the \" in "b", which is another string.
    w.known("tail-slash", "abcdefgh\\")
    text = '{"a":"abcdefgh\\\\","b":"abcdefgh\\"x"}\n'
    n = w.add(name, text)
    w.run(gitleaks)
    out = w.redacted(n)
    assert json.loads(out) == {"a": "[REDACTED:tail-slash]", "b": 'abcdefgh"x'}


def test_a_non_ascii_value_is_replaced_as_its_unescaped_json_spelling(w, gitleaks):
    # Written with non-ASCII kept (ensure_ascii off), the escaped spelling
    # holds the é as it is.
    w.known("accent", "abcdéfgh\\")
    n = w.add(
        "a.json", json.dumps({"a": "abcdéfgh\\", "b": 'abcdéfgh"x'}, ensure_ascii=False)
    )
    w.run(gitleaks)
    assert json.loads(w.redacted(n)) == {"a": "[REDACTED:accent]", "b": 'abcdéfgh"x'}


@pytest.mark.parametrize("name", ["a.jsonl", "a.json"])
def test_a_line_that_is_not_json_is_redacted_as_text(w, gitleaks, name):
    # The JSON line has the escaped spelling; the other is plain text and
    # holds the value as it is. A .json file that is not one document is
    # read a line at a time, as the scan's view of it is.
    w.known("tail-slash", "abcdefgh\\")
    n = w.add(name, '{"a":"abcdefgh\\\\"}\nnot json: abcdefgh\\ end\n')
    w.run(gitleaks)
    out = w.redacted(n)
    assert out == '{"a":"[REDACTED:tail-slash]"}\nnot json: [REDACTED:tail-slash] end\n'


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
    # A session start or end counts them; a manual run says where.
    assert "1 secret that cannot be redacted; make scrub-check lists them" in printed
    assert "cannot be redacted in" not in printed
    assert PAT not in w.redacted(n)
    assert "done 3" not in w.actions() and w.actions()[-1].startswith("done 2 ")
    w.run(gitleaks, "--manual")
    assert "(odd): cannot be redacted in 2 files. Rotate it" in capsys.readouterr().out


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


def test_report_only_identifiers_are_not_reported(w, gitleaks, capsys):
    base = "ZZq7Rk2LmP9xW4vT8sN1bH6cJ3fD5g"  # pragma: allowlist secret
    ids = [base + c * 4 for c in "abcdefg"]
    w.add(
        "a.jsonl",
        line(event_id=ids[0], node_id=ids[1])
        + "\n"
        # A tool output holding JSON, escaped once more.
        + line(t=json.dumps({"tool_use_id": ids[2], "Sid": ids[3]}))
        + "\n"
        + transcript(f"256 SHA256:{ids[4]} key", f"github.com ssh-ed25519 {ids[5]}"),
    )
    # The same value is an identifier in one place and not in another.
    w.add("b.jsonl", line(event_id=ids[6]) + "\n" + transcript(f"value {ids[6]}"))
    w.run(gitleaks, "--manual", "--entropy")
    printed = capsys.readouterr().out
    assert printed.count("a random looking string") == 1
    assert f"...{ids[6][-4:]} in " in printed and "b.jsonl" in printed


@pytest.mark.parametrize(
    "before",
    [
        "ssh-ed25519 ",
        "ssh-rsa ",
        "ssh-dss ",
        "ecdsa-sha2-nistp256 ",
        "SHA256:",
        '"Sid":"',
        "event_id: ",
    ],
)
def test_each_identifier_context_is_benign(before):
    value = "ZZq7Rk2LmP9xW4vT8sN1bH6cJ3fD5gab"  # pragma: allowlist secret
    assert scrub.benign(f"x {before}{value} y", value)


@pytest.mark.parametrize(
    "before", ['"passwordSid":"', "my_event_id: ", "ssh-other ", "nistp256 "]
)
def test_a_near_miss_is_not_benign(before):
    value = "ZZq7Rk2LmP9xW4vT8sN1bH6cJ3fD5gab"  # pragma: allowlist secret
    assert not scrub.benign(f"x {before}{value} y", value)


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
    assert redact == {"1": {long: ("r", None, long)}}
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


# Private keys, matched against the file itself.

# Made up key material, built here so no literal has a key's shape.
MATERIAL = base64.b64encode(bytes(range(256)) * 2).decode()
KEY_LINES = [MATERIAL[i : i + 64] for i in range(0, 256, 64)]
BEGIN = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
END = "-----END OPENSSH " + "PRIVATE KEY-----"
KEY = "\n".join([BEGIN, *KEY_LINES, END])
KEY_TAIL = "".join(KEY_LINES)[-4:]


@pytest.mark.parametrize(
    "rel, text",
    [
        ("notes.md", f"the key:\n{KEY}\nend"),
        ("a.jsonl", line(t=f"cat id\n{KEY}\n") + "\n"),
        # A tool's output held as a JSON string inside a JSON string.
        ("b.jsonl", line(t=json.dumps({"output": KEY})) + "\n"),
        # A file read with its line numbers, both ways Claude Code writes them.
        (
            "c.jsonl",
            line(
                t="\n".join(f"{i:6}\u2192{x}" for i, x in enumerate(KEY.split("\n"), 1))
            )
            + "\n",
        ),
        (
            "d.jsonl",
            line(t="\n".join(f"{i:6}\t{x}" for i, x in enumerate(KEY.split("\n"), 1)))
            + "\n",
        ),
        ("e.jsonl", json.dumps({"t": KEY}, ensure_ascii=True) + "\n"),
        ("f.txt", KEY.replace("\n", "\r\n")),
        ("g.jsonl", line(t=KEY.replace("\n", "\r\n")) + "\n"),
        # The same key escaped once and twice on one line: each block keeps
        # its own spelling, though one is the other escaped once more.
        ("h.jsonl", line(t=KEY, u=json.dumps({"output": KEY})) + "\n"),
    ],
)
def test_a_private_key_is_redacted_however_it_is_escaped(w, gitleaks, rel, text):
    n = w.add(rel, text)
    assert w.run(gitleaks) == 0
    out = w.redacted(n)
    assert all(x not in out for x in KEY_LINES) and "BEGIN OPENSSH" not in out
    assert f"[REDACTED:private-key:...{KEY_TAIL}]" in out
    assert scrub.still_valid(rel, text, out)
    (entry,) = w.ledger()["entries"]
    assert entry["rule"] == "private-key" and entry["tail"] == KEY_TAIL


@pytest.mark.parametrize("name", ["a.jsonl", "a.json"])
def test_a_raw_key_over_plain_lines_is_redacted_beside_its_escaped_copy(
    w, gitleaks, name
):
    # Escaped on a JSON line, and raw over the plain lines after it: both go,
    # and every line keeps its number.
    text = line(t=KEY) + "\n" + KEY + "\n" + line(t="after") + "\n"
    n = w.add(name, text)
    w.run(gitleaks)
    out = w.redacted(n)
    assert all(x not in out for x in KEY_LINES) and "BEGIN OPENSSH" not in out
    assert out.count("\n") == text.count("\n")
    assert out.split("\n")[-2] == line(t="after")


def test_a_raw_key_over_a_json_line_is_left_and_said(w, gitleaks, capsys):
    # A key line of digits only is a JSON number: replacing the block would
    # leave it empty, so the block is left and its first line named.
    digits = "1234567890" * 7
    key = "\n".join([BEGIN, KEY_LINES[0], digits, *KEY_LINES[1:], END])
    n = w.add("a.jsonl", f"{key}\n" + line(t=f"token {PAT}") + "\n")
    w.run(gitleaks, "--manual")
    out = w.redacted(n)
    assert digits in out and PAT not in out
    assert re.search(
        r"\(private-key \.\.\..{4}\): cannot be redacted in .*a\.jsonl \(line 1\)",
        capsys.readouterr().out,
    )


def test_one_key_written_two_ways_is_one_entry(w, gitleaks):
    w.add("a.md", KEY)
    w.add("b.jsonl", line(t=json.dumps({"output": KEY})) + "\n")
    w.run(gitleaks)
    (entry,) = w.ledger()["entries"]
    assert len(entry["files"]) == 2


def test_part_of_a_key_is_redacted_and_a_legacy_one_with_its_headers(w, gitleaks):
    begin = "-----BEGIN RSA " + "PRIVATE KEY-----"
    head = w.add("a.md", "$ head -3 id\n" + "\n".join([BEGIN, *KEY_LINES[:2]]) + "\n$ ")
    legacy = "\n".join(
        [
            begin,
            "Proc-Type: 4,ENCRYPTED",
            "DEK-Info: AES-128-CBC,0011",
            "",
            *KEY_LINES,
            "-----END RSA " + "PRIVATE KEY-----",
        ]
    )
    old = w.add("b.md", legacy)
    w.run(gitleaks)
    assert KEY_LINES[0] not in w.redacted(head) and w.redacted(head).endswith("]\n$ ")
    assert w.redacted(old) == f"[REDACTED:private-key:...{KEY_TAIL}]"


def test_a_key_named_in_prose_is_no_key(w, gitleaks, monkeypatch, capsys):
    # gitleaks' own rule runs over the decoded view, from a header in one
    # string to one in another, which no file holds as it is: its hits are
    # left out, and nothing here has key material.
    monkeypatch.setenv(
        "FAKE_RULES",
        json.dumps(
            [
                *RULES,
                [
                    "private-key",
                    r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]{20,}?KEY-----",
                ],
            ]
        ),
    )
    prose = transcript(
        f"The key file starts with {BEGIN} and then",
        f"{BEGIN}\nMIIE...\n{END}",
        f"and ends with {END}.",
        f'grep -q "{BEGIN}" id',
        f"{BEGIN}\n{'ABCDEFGHIJKLMNOPQRSTUVWXYZ' * 3}\nend",
    )
    w.add("a.jsonl", prose)
    w.run(gitleaks, "--manual")
    printed = capsys.readouterr().out
    assert "cannot be redacted in" not in printed and "private-key" not in printed
    assert "0 keep something that cannot be redacted" in printed
    assert w.ledger()["entries"] == []
    assert scrub.key_blocks(prose) == {}


@pytest.mark.parametrize(
    "rel, text",
    [
        ("a.md", KEY),
        ("b.jsonl", line(t=KEY) + "\n"),
        # Inside a JSON string inside a JSON string, which no form of the
        # value matches: found as a block, and known by its material.
        ("c.jsonl", line(t=json.dumps({"output": KEY})) + "\n"),
    ],
)
def test_a_key_is_found_once_when_a_known_secret_holds_it(w, gitleaks, rel, text):
    w.known("ssh-key", KEY)
    n = w.add(rel, text)
    w.run(gitleaks)
    assert f"[REDACTED:ssh-key:...{KEY[-4:]}]" in w.redacted(n)
    assert all(x not in w.redacted(n) for x in KEY_LINES)
    assert [e["rule"] for e in w.ledger()["entries"]] == ["ssh-key"]


def test_part_two_known_keys_share_is_neither(w, gitleaks):
    other = "\n".join([BEGIN, KEY_LINES[0], *(x[::-1] for x in KEY_LINES[1:]), END])
    w.known("ssh-key", KEY)
    w.known("other-key", other)
    n = w.add("a.md", "\n".join([BEGIN, KEY_LINES[0]]) + "\n$ ")
    w.run(gitleaks)
    assert KEY_LINES[0] not in w.redacted(n)
    (entry,) = w.ledger()["entries"]
    assert entry["rule"] == "private-key" and entry["known"] is None


def test_known_key_with_separate_partial_copy(w, gitleaks, capsys):
    w.known("ssh-key", KEY)
    part = "\n".join([BEGIN, *KEY_LINES[:2]])
    n = w.add(
        "a.jsonl",
        line(t=f"cat id\n{KEY}\n") + "\n" + line(t=f"head -3 id\n{part}\n") + "\n",
    )
    w.run(gitleaks, "--manual")
    out = w.redacted(n)
    assert all(x not in out for x in KEY_LINES) and "BEGIN OPENSSH" not in out
    assert [e["rule"] for e in w.ledger()["entries"]] == ["ssh-key"]
    assert w.actions()[-2:] == ["replace 1", f"done 1 {len(out.encode())}"]
    assert "cannot be redacted in" not in capsys.readouterr().out


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
    assert (
        "no secret #7 in the ledger, so nothing was marked" in capsys.readouterr().out
    )


def test_a_tier_one_secret_is_rotated_once_its_value_changes(w, gitleaks, capsys):
    w.known("gh-broker-token", KNOWN)
    w.env("OTHER=" + "another-known-value-99")
    w.add("a.jsonl", transcript(KNOWN, "another-known-value-99", PAT))
    w.run(gitleaks)
    assert (
        "3 secrets not rotated yet; make scrub-check lists them"
        in capsys.readouterr().out
    )
    (w.work / "manifest").write_text("\n")
    w.run(gitleaks, "--manual")
    assert (
        "marked rotated on its own once gh-broker-token changes"
        in capsys.readouterr().out
    )
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
    # A session start or end: the count only, to keep it short.
    assert (
        capsys.readouterr().out
        == "scrub: 1 secret not rotated yet; make scrub-check lists them\n"
    )
    assert w.actions() == []
    assert w.run(gitleaks, "--manual") == 0
    printed = capsys.readouterr().out
    assert f"secret #1 (github-pat ...{PAT[-4:]}): found before, first on" in printed
    assert "in 1 file, and not rotated yet. Rotate it\n" in printed


def test_a_scan_of_one_folder_lists_only_its_open_secrets(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT))
    w.add("b.jsonl", transcript(AWS))
    w.run(gitleaks)
    w.manifest = []
    w.n = 0
    w.add("b.jsonl", transcript("clean now"))
    capsys.readouterr()
    w.run(gitleaks, "--manual", "--check", "--scope", "/some/folder")
    printed = capsys.readouterr().out
    assert "aws-access-token" in printed and "github-pat" not in printed
    assert "(only the conversations that ran in /some/folder); dry run" in printed


def test_nothing_open_says_nothing(w, gitleaks, capsys):
    w.add("a.jsonl", transcript("clean"))
    w.run(gitleaks)
    assert "not rotated" not in capsys.readouterr().out


def test_rotated_takes_several_ids_or_all(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT, AWS))
    w.add("b.jsonl", transcript(KNOWN))
    w.known("gh-broker-token", KNOWN)
    w.run(gitleaks)
    capsys.readouterr()
    assert scrub.main(["rotated", "1", "2"]) == 0
    printed = capsys.readouterr().out
    assert "secret #1" in printed and "secret #2" in printed
    statuses = {e["id"]: e["status"] for e in w.ledger()["entries"]}
    assert statuses == {1: "rotated", 2: "rotated", 3: "open"}
    assert scrub.main(["rotated", "all"]) == 0
    assert "secret #3" in capsys.readouterr().out
    assert all(e["status"] == "rotated" for e in w.ledger()["entries"])
    assert scrub.main(["rotated", "all"]) == 0
    assert "no secret is open, so nothing was marked" in capsys.readouterr().out


def test_rotated_marks_nothing_when_one_id_is_wrong(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT))
    w.run(gitleaks)
    assert scrub.main(["rotated", "1", "x"]) == 2
    assert (
        "no secret #x in the ledger, so nothing was marked" in capsys.readouterr().out
    )
    assert w.ledger()["entries"][0]["status"] == "open"


def test_secrets_are_said_in_number_order(w, gitleaks, capsys):
    """#9 before #10, in a scan's lines and in the list, not as text sorts."""
    (w.state / "ledger.json").write_text(json.dumps({"next": 9, "entries": []}))
    w.context("now 100000", f"live {UUID_B}")  # waiting, so the scan says them
    w.add(f"p/{UUID_B}.jsonl", transcript(PAT, AWS))
    w.run(gitleaks, "--manual")
    printed = capsys.readouterr().out
    nine, ten = printed.index("secret #9 ("), printed.index("secret #10 (")
    assert nine < ten
    scrub.main(["list"])
    rows = [line.split()[0] for line in capsys.readouterr().out.splitlines()[1:-1]]
    assert rows == ["9", "10"]


def test_list_is_a_table_of_the_open_secrets(w, gitleaks, capsys):
    w.add("a.jsonl", transcript(PAT, AWS))
    w.add("b.jsonl", transcript(PAT))
    w.run(gitleaks)
    scrub.main(["rotated", "2"])
    capsys.readouterr()
    assert scrub.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "ID",
        "TYPE",
        "ENDS",
        "IN",
        "FIRST",
        "SEEN",
        "LAST",
        "SEEN",
        "FILES",
        "STATUS",
    ]
    assert lines[1].split()[:3] == ["1", "aws-access-token", AWS[-4:]]
    assert lines[1].split()[-2:] == ["1", "open"]
    assert not any(line.split()[0] == "2" for line in lines[1:-1])  # rotated
    assert (
        lines[-1]
        == "scrub: 1 secret open, 1 secret rotated; once rotated: make scrub-rotated ID=<n>"
    )
    assert scrub.main(["list", "--all", "--files"]) == 0
    printed = capsys.readouterr().out
    assert re.search(
        rf"^2 +github-pat +{PAT[-4:]} .* 2 +rotated \d{{4}}-\d\d-\d\d$",
        printed,
        re.MULTILINE,
    )
    assert f"      {w.home}/a.jsonl\n      {w.home}/b.jsonl\n" in printed


def test_list_with_nothing_to_show(w, gitleaks, capsys):
    assert scrub.main(["list"]) == 0
    assert "no secret in the ledger yet" in capsys.readouterr().out
    w.add("a.jsonl", transcript(KNOWN))
    w.known("gh-broker-token", KNOWN)
    w.run(gitleaks)
    scrub.main(["rotated", "all"])
    capsys.readouterr()
    assert scrub.main(["list"]) == 0
    assert (
        "no secret is open; make scrub-list ALL=1 shows the rotated ones"
        in capsys.readouterr().out
    )
    assert scrub.main(["list", "--all"]) == 0
    assert "gh-broker-token" in capsys.readouterr().out


# show: what is around a secret's placeholder, to tell what it was.


def redact_two(w, gitleaks):
    """A PAT (with a tail) in two files, and two short API keys (no tail) in
    a third; then the copies host/scrub would make, by original path."""
    other = "Yp7Kd93L" + "mx0Pw2Rt"
    w.add("a.jsonl", transcript(f"export GITHUB_TOKEN={PAT} for the deploy"))
    w.add("b.md", f"step 1\n\x1b[31mtoken\x1b[0m: {PAT}\n")
    w.add("c.md", f'config: api_key: "{API}"\nother: api_key: "{other}"\n')
    w.run(gitleaks)
    for e in scrub.read_manifest(w.work / "manifest"):
        (w.work / "files" / e.n).write_text(w.redacted(e.n))


def test_files_lists_where_each_secret_was(w, gitleaks, capsys):
    redact_two(w, gitleaks)
    capsys.readouterr()
    assert scrub.main(["files", "1", "1", "99"]) == 0
    assert capsys.readouterr().out == f"{w.home}/a.jsonl\n{w.home}/b.md\n"


def test_show_prints_the_text_around_a_placeholder(w, gitleaks, capsys):
    redact_two(w, gitleaks)
    capsys.readouterr()
    assert scrub.main(["show", "1"]) == 0
    printed = capsys.readouterr().out
    mark = f"[REDACTED:github-pat:...{PAT[-4:]}]"
    assert printed.startswith(f"#1 github-pat ...{PAT[-4:]} (open), first seen ")
    assert ", 2 files\n" in printed
    assert f"export GITHUB_TOKEN={mark} for the deploy" in printed
    # Escape sequences from a file never reach the terminal.
    assert "\x1b" not in printed and f" [31mtoken [0m: {mark}" in printed
    assert PAT not in printed


def test_show_says_when_a_placeholder_could_be_any_of_its_kind(w, gitleaks, capsys):
    redact_two(w, gitleaks)
    capsys.readouterr()
    keys = [e["id"] for e in w.ledger()["entries"] if e["rule"] == "generic-api-key"]
    scrub.main(["show", str(keys[0])])
    printed = capsys.readouterr().out
    assert (
        "with no tail, 2 placeholders like it here could be any secret of this kind"
        in printed
    )
    # Each one shown, the other within the text around it.
    assert printed.count("\n    ...") == 2


def test_show_with_a_file_gone_redacted_since_or_rotated(w, gitleaks, capsys):
    redact_two(w, gitleaks)
    (w.work / "files" / "2").write_text("rewritten, nothing here\n")
    # a.jsonl no longer copied: gone from the host.
    (w.work / "manifest").write_text("".join(f"{m}\n" for m in w.manifest[1:]))
    capsys.readouterr()
    scrub.main(["show", "1", "99"])
    printed = capsys.readouterr().out
    assert f"{w.home}/a.jsonl: gone, or could not be read" in printed
    assert f"{w.home}/b.md: no placeholder for it" in printed
    assert "scrub: no secret #99 in the ledger" in printed
    scrub.main(["rotated", "1"])
    capsys.readouterr()
    scrub.main(["show", "1"])
    assert "(rotated 20" in capsys.readouterr().out


def test_show_limits_to_five_placeholders(w, gitleaks, capsys):
    n = w.add("a.md", "".join(f"use {i}: {PAT}\n" for i in range(6)))
    w.run(gitleaks)
    (w.work / "files" / n).write_text(w.redacted(n))
    capsys.readouterr()
    scrub.main(["show", "1"])
    printed = capsys.readouterr().out
    assert printed.count("\n    ...") == 5 and "    and 1 more\n" in printed


def test_show_says_when_another_secret_ends_the_same(w, gitleaks, capsys):
    # Two tokens of one rule with the same last four characters.
    twin = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4Q7r8"  # pragma: allowlist secret
    n = w.add("a.md", f"one {PAT}\ntwo {twin}\n")
    w.run(gitleaks)
    (w.work / "files" / n).write_text(w.redacted(n))
    capsys.readouterr()
    scrub.main(["show", "1"])
    assert (
        "; #2 ends the same, so a placeholder here could be either"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("state", ["redacted", "rewritten", "gone"])
def test_show_prints_no_escape_from_a_file_name(w, gitleaks, capsys, state):
    # Two spaces and a literal \n are kept as they are: only what is not
    # printable becomes a space, so the name still points at its file.
    n = w.add("odd\x1b[2J  re\\n.md", f"token {PAT}\n")
    w.run(gitleaks)
    (w.work / "files" / n).write_text(
        w.redacted(n) if state == "redacted" else "nothing\n"
    )
    if state == "gone":
        (w.work / "manifest").write_text("")
    capsys.readouterr()
    scrub.main(["show", "1"])
    printed = capsys.readouterr().out
    assert "\x1b" not in printed and "odd [2J  re\\n.md" in printed


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


# The rules of host/scrub-gitleaks.toml this repository adds, applied with
# Python's re as gitleaks applies them with Go's (the same syntax for these):
# the secret group, then the rule's allowlist on the secret. The real
# gitleaks runs them in docs/SCRUB.md, Testing.

GITLEAKS_RULES = {
    r["id"]: r
    for r in tomllib.loads((REPO_ROOT / "host/scrub-gitleaks.toml").read_text())[
        "rules"
    ]
}


def rule_hits(rule_id: str, text: str) -> list[str]:
    rule = GITLEAKS_RULES[rule_id]
    allow = [re.compile(p) for a in rule.get("allowlists", []) for p in a["regexes"]]
    found = [m.group(rule["secretGroup"]) for m in re.finditer(rule["regex"], text)]
    return [s for s in found if not any(a.search(s) for a in allow)]


PASSWORD_RULES = (
    "password-assignment",
    "password-assignment-quoted",
    "password-assignment-single-quoted",
)
# The key word and a value made of plain fixture words around the
# separator a case is about, so nothing here reads as a password.
KW = "pass" + "word"
# An escaped double and an escaped single quote.
BSQ = '\\"'
BSA = "\\'"


def value(sep: str) -> str:
    return f"fixture{sep}value{sep}0001"


def password_hits(text: str) -> list[str]:
    return [hit for rule in PASSWORD_RULES for hit in rule_hits(rule, text)]


@pytest.mark.parametrize(
    "text, secret",
    [
        (f'REDIS_{KW.upper()}="{value("^")}"', value("^")),
        (f"export PG{KW.upper()}={value('&')}", value("&")),
        (f"{KW}: {value('-')}", value("-")),
        (f"admin_{KW} := {value('+')}", value("+")),
        # A shell's backslash pair is part of an unquoted value, not its end,
        # an escaped space too.
        (f"PG{KW.upper()}={value(chr(92) + '!')} pg_dump", value("\\!")),
        (f"{KW}={value(chr(92) + ' ')} next", value("\\ ")),
        # A value shaped like a CircleCI mask is still taken: from the value
        # alone it cannot be told from a password shaped the same.
        (f"DOCKERHUB_{KW.upper()} = xxxxab12", "xxxxab12"),
        # A value that only starts with $ is a value, not a reference.
        (f"{KW}=${value('!')}", "$" + value("!")),
        (f'{KW}: "${value("$")}"', "$" + value("$")),
        # A quoted value is taken whole, up to its own closing quote.
        (f"db_pwd: '{value(', ')}'", value(", ")),
        (f'"{KW}": "{value(" (b) {c}; ")}"', value(" (b) {c}; ")),
        (f"""{KW}: "{value("'")}" """, value("'")),
        (f"""{KW}: '{value('"')}' """, value('"')),
        # An escaped quote of its own kind is part of it too.
        (f'{KW}="{value(BSQ)}"', value(BSQ)),
        (f"{KW}='{value(BSA)}'", value(BSA)),
        # A shell has no \' in single quotes: with no later quote, the value
        # ends at that quote, its backslash kept.
        (f"{KW}='{value('-')}\\'", value("-") + "\\"),
        # With a later quote, more than the shell's value (fixture\) is
        # taken, so nothing of it is left.
        (f"{KW}='fixture\\'; echo 'ok'", "fixture\\'; echo "),
    ],
)
def test_a_password_is_found_whatever_it_holds(text, secret):
    assert password_hits(text) == [secret]


@pytest.mark.parametrize(
    "quote, inner, rule",
    [
        ('"', "'", "password-assignment-quoted"),
        ("'", '"', "password-assignment-single-quoted"),
        ('"', '\\"', "password-assignment-quoted"),
        ("'", "\\'", "password-assignment-single-quoted"),
    ],
)
def test_a_quoted_password_is_redacted_whole(
    w, gitleaks, monkeypatch, quote, inner, rule
):
    rules = [[r, GITLEAKS_RULES[r]["regex"]] for r in PASSWORD_RULES]
    monkeypatch.setenv("FAKE_RULES", json.dumps(rules))
    secret = value(f" {inner}, ")
    n = w.add("a.jsonl", line(t=f"db_pwd: {quote}{secret}{quote} done") + "\n")
    w.run(gitleaks)
    placeholder = f"[REDACTED:{rule}:...{secret[-4:]}]"
    assert w.redacted(n) == line(t=f"db_pwd: {quote}{placeholder}{quote} done") + "\n"


@pytest.mark.parametrize(
    "escaped, tail",
    [
        # 21 characters as written, 17 once read: too short to show a tail.
        ('fixt\\"ure\\"val\\"ue\\"0', ""),
        ('fixture\\"value\\"00001234', "1234"),
    ],
)
def test_a_quoted_password_is_known_by_its_value(
    w, gitleaks, monkeypatch, escaped, tail
):
    rules = [[r, GITLEAKS_RULES[r]["regex"]] for r in PASSWORD_RULES]
    monkeypatch.setenv("FAKE_RULES", json.dumps(rules))
    plain = escaped.replace("\\", "")
    n = w.add("notes.md", f'{KW}="{escaped}"\nit was {plain} then\n')
    w.run(gitleaks)
    placeholder = "[REDACTED:password-assignment-quoted" + (
        f":...{tail}]" if tail else "]"
    )
    # Both spellings are gone, and they are one secret.
    assert w.redacted(n) == f'{KW}="{placeholder}"\nit was {placeholder} then\n'
    (entry,) = w.ledger()["entries"]
    assert entry["tail"] == tail


@pytest.mark.parametrize(
    "text",
    [
        # A shell keeps the backslash in single quotes: two values, not one.
        f"{KW}='fixture\\value-0001'\n{KW}='fixturevalue-0001'\n",
        # \n is a newline in Python and two characters in a shell: neither
        # is guessed, so it is not merged with an n.
        f'{KW}="fixture\\nvalue-0001"\n{KW}="fixturenvalue-0001"\n',
    ],
)
def test_only_the_values_own_quote_is_unescaped(w, gitleaks, monkeypatch, text):
    rules = [[r, GITLEAKS_RULES[r]["regex"]] for r in PASSWORD_RULES]
    monkeypatch.setenv("FAKE_RULES", json.dumps(rules))
    n = w.add("notes.md", text)
    w.run(gitleaks)
    assert len(w.ledger()["entries"]) == 2
    assert "fixture" not in w.redacted(n)


def test_an_unquoted_password_is_redacted_as_a_shell_reads_it_too(
    w, gitleaks, monkeypatch
):
    rules = [[r, GITLEAKS_RULES[r]["regex"]] for r in PASSWORD_RULES]
    monkeypatch.setenv("FAKE_RULES", json.dumps(rules))
    n = w.add(
        "notes.md",
        f"{KW}=fixture\\!value-00001234 then\nit was fixture!value-00001234 here\n",
    )
    w.run(gitleaks)
    placeholder = "[REDACTED:password-assignment:...1234]"
    assert w.redacted(n) == f"{KW}={placeholder} then\nit was {placeholder} here\n"
    (entry,) = w.ledger()["entries"]
    assert entry["tail"] == "1234"


@pytest.mark.parametrize(
    "text",
    [
        "PGPASSWORD=$(aws rds generate-db-auth-token)",
        "password: ${DB_PASSWORD}",
        "password = getpass()",
        "password=os.environ['X']",
        "password = self.password",
        "password = new_password",
        "password: newPassword",
        "PASSWORD=REQUIRED",
        "password: ********",
        "password: <your password>",
        "password: [REDACTED:password-assignment:...wE5x]",
        "password_file: /run/secrets/db",
        "password_url: https://vault.example/x",
        "password: changeme",
        "a password is hard",
        'password: "${DB_PASSWORD}"',
        'password: "$DB_PASSWORD"',
        "password: '$(aws rds generate-db-auth-token)'",
        'password: "${{ secrets.DB }}"',
        "password: '{{ vault.db }}'",
        'password: "<your password>"',
        'password: "[REDACTED:password-assignment-quoted:...wE5x]"',
        'password = "new_password"',  # pragma: allowlist secret
        'password: "********"',
    ],
)
def test_a_reference_or_placeholder_is_no_password(text):
    assert password_hits(text) == []


@pytest.mark.parametrize(
    "hash_",
    [
        # The shapes only, filled with fixture words.
        "$2y$10$" + ("fixture.hash." * 5)[:53],
        "$6$rounds=5000$fixture$" + "a" * 86,
        "$5$fixture$" + "B" * 43,
        "$argon2id$v=19$m=65536,t=3,p=4$" + "Zml4dHVyZQ" + "$" + "fixtureHashValue",
    ],
)
def test_a_password_hash_is_found(hash_):
    assert rule_hits("password-hash", f'"account-1","{hash_}",') == [hash_]


def test_a_dollar_amount_is_no_password_hash():
    assert rule_hits("password-hash", "it costs $2 or $10, $5 each") == []
