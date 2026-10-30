"""Which Claude Code make targets in host/workbench.mk have voice.

Every claude target passes VOICE through as WORKBENCH_VOICE (VOICE=0 turns
it off for one run), the unattended ones included: a run is often started
with someone still at the keyboard. Only the plain ones force it off. The
recipes are read as text rather than run, because neither container `make
coverage` runs the tests in has make, and a recipe is one line to check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKBENCH_MK = Path(__file__).resolve().parents[1] / "host/workbench.mk"

# `name:` opening a rule (not `name :=` or `name ?=`), and the account
# targets' `claude-$(1)-...` spelled as they are inside the define.
RULE = re.compile(r"^(?P<target>[\w$().-]+):(?!=)")

# What starts a session with voice when VOICE says so: `$(VOICE)`, or
# `$$(VOICE)` inside the account define, which eval turns into `$(VOICE)`.
VOICE_FROM_VOICE = re.compile(r"^@WORKBENCH_VOICE=\$\$?\(VOICE\) ")


def recipes() -> dict[str, str]:
    """Each rule's first recipe line, by target."""
    found: dict[str, str] = {}
    target = None
    for line in WORKBENCH_MK.read_text().splitlines():
        if match := RULE.match(line):
            target = match["target"]
        elif line.startswith("\t") and target is not None:
            found.setdefault(target, line.strip())
    return found


@pytest.mark.parametrize(
    "target",
    [
        "claude",
        "claude-remote",
        "claude-assisted",
        "claude-unattended",
        "claude-$(1)",
        "claude-$(1)-remote",
        "claude-$(1)-assisted",
        "claude-$(1)-unattended",
    ],
)
def test_voice_follows_voice(target: str) -> None:
    recipe = recipes()[target]
    assert VOICE_FROM_VOICE.match(recipe), recipe


@pytest.mark.parametrize("target", ["claude-plain", "claude-$(1)-plain"])
def test_plain_has_no_voice(target: str) -> None:
    assert recipes()[target].startswith("@WORKBENCH_VOICE=0 ")


def test_unattended_is_still_unattended() -> None:
    """The voice change kept the level, the hours and the cap's arguments."""
    for target in ("claude-unattended", "claude-$(1)-unattended"):
        recipe = recipes()[target]
        assert "--level unattended" in recipe
        assert "HOURS_ARG)" in recipe


def test_scrub_list_flags_take_exactly_1() -> None:
    """ALL and FILES give their flag only for the value 1, so a repository's
    own variable of that name (a list of files, say, or "1 2") does not.
    Checked with make itself in the PR that added it; neither container
    `make coverage` runs has make, so here the text stands in."""
    assert recipes()["scrub-list"] == (
        "@$(WORKBENCH) scrub list $(call scrub_flag,$(ALL),--all) $(call scrub_flag,$(FILES),--files)"
    )
    text = WORKBENCH_MK.read_text()
    assert "scrub_flag = $(if $(filter 1,$(1)),$(if $(word 2,$(1)),,$(2)))\n" in text


@pytest.mark.parametrize("target, check", [("scrub", ""), ("scrub-check", " --check")])
def test_scrub_dir_gives_dir_only_when_set(target: str, check: str) -> None:
    """SCRUB_DIR adds --dir with the folder quoted from the environment,
    and nothing when it is unset or empty (make's $(if) is false for an
    empty value). Text stands in for make, as above."""
    assert recipes()[target] == (
        f'@$(WORKBENCH) scrub{check} $(if $(ENTROPY),--entropy) $(if $(SCRUB_DIR),--dir "$$SCRUB_DIR")'
    )
    text = WORKBENCH_MK.read_text()
    assert "\nSCRUB_DIR ?=\n" in text and "\nexport ID SCRUB_DIR\n" in text
