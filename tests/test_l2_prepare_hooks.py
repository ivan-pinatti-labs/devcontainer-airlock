"""Tests for images/l2/bin/l2-prepare-hooks.

pre-commit itself never runs: subprocess.run is replaced by a recorder, and
pre-commit's store is a small sqlite database built here, with the one table
the script reads.
"""

from __future__ import annotations

import sqlite3
import subprocess

import pytest
from conftest import load

SCRIPT = "images/l2/bin/l2-prepare-hooks"
LIBRARY = "https://github.com/ivan-pinatti-labs/pre-commit-checklists"


@pytest.fixture
def hooks(tmp_path, monkeypatch):
    module = load(SCRIPT, "l2_prepare_hooks")
    calls = []

    def run(args, capture_output, text):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "store"))
    module.calls = calls
    return module


def make_store(path, rows):
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / "db.db")
    db.execute("CREATE TABLE repos (repo TEXT, ref TEXT, path TEXT)")
    db.executemany("INSERT INTO repos VALUES (?, ?, ?)", rows)
    db.commit()
    db.close()


def test_run_stops_on_a_failed_install(tmp_path, monkeypatch, capsys):
    module = load(SCRIPT, "l2_prepare_hooks")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda args, capture_output, text: subprocess.CompletedProcess(
            args, 3, "out\n", "err\n"
        ),
    )
    with pytest.raises(SystemExit) as e:
        module.run(["install-hooks"])
    assert e.value.code == 1
    assert capsys.readouterr().err == "out\nerr\n"


def test_without_a_store_only_the_own_hooks_are_installed(hooks, tmp_path, monkeypatch):
    (tmp_path / "work/.pre-commit-config.yaml").write_text("")
    monkeypatch.delenv("PRE_COMMIT_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert hooks.main() is None
    assert hooks.calls == [
        ["pre-commit", "install-hooks", "--config", ".pre-commit-config.yaml"]
    ]


def test_installs_every_checklist_the_config_names(hooks, tmp_path):
    clone = tmp_path / "clone"
    (clone / "checklists").mkdir(parents=True)
    (clone / "checklists/checklist-basic.yaml").write_text("repos: []\n")
    local_scripts = tmp_path / "lib/scripts"
    local_scripts.mkdir(parents=True)
    (tmp_path / "lib/checklists").mkdir()
    (tmp_path / "lib/checklists/checklist-spell.yaml").write_text("repos: []\n")
    entry = str(local_scripts / "run-checklist.sh")
    (tmp_path / "work/.pre-commit-config.yaml").write_text(f"""
repos:
  - repo: {LIBRARY}
    rev: v1
    hooks:
      - id: checklist-basic
      - id: checklist-missing
  - repo: {LIBRARY}
    rev: v0
    hooks:
      - id: checklist-basic
  - repo: https://example.test/other
    rev: v2
    hooks:
      - id: ruff
  - repo: meta
    hooks:
      - id: checklist-looks-like-one
  - repo: local
    hooks:
      - id: checklist-spell
        entry: {entry}
        args: [checklist-spell]
      - id: checklist-gone
        entry: {entry}
        args: [checklist-gone]
      - id: no-args
        entry: {entry}
      - id: something-else
        entry: make coverage
        args: [x]
      - id: bare
""")
    make_store(tmp_path / "store", [(LIBRARY, "v1", str(clone))])
    assert hooks.main() is None
    configs = [c[-1] for c in hooks.calls]
    assert configs == [
        ".pre-commit-config.yaml",
        str(clone / "checklists/checklist-basic.yaml"),
        str(local_scripts / ".." / "checklists" / "checklist-spell.yaml"),
    ]
