"""Keep .github/pin-only.yml in step with the pins it is meant to grade.

`Pin Only` (ivan-pinatti-labs/gh-actions) is what lets a dependency bot's
pull request merge with nobody reading it, and it grades only what
.github/pin-only.yml names. A script that gains an image pin Renovate moves,
a rule dropped from the file, or a pin rewritten into a shape its grammar
does not read would each send every bump of that pin back to a person
without anything saying so. These tests say so.

What the grammars accept and refuse is tested where they live, in
gh-actions (tests/test_pin_only_shell_image.py and
tests/test_pin_only_tag_commit.py there). This file checks only that this
repository's configuration points them at the right lines.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((REPO_ROOT / ".github/pin-only.yml").read_text())

ANNOTATION = re.compile(r"^#\s*renovate:")
# The shape gh-actions' shell_image_pin reads: an assignment alone on its
# line, of an image pinned by tag and digest, bare or in double quotes.
SHELL_IMAGE_PIN = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_]*)=(?P<quote>"?)[A-Za-z0-9._/-]+:'
    r"[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}(?P=quote)[ \t]*$"
)
# Any assignment that looks like an image pin, whether or not the grammar
# could read it: what a script would write when it gains one.
LOOSE_IMAGE_ASSIGNMENT = re.compile(r'^[A-Z][A-Z0-9_]*="?[^\s"]+@sha256:')
# agent-policy's `ARG AGENT_POLICY_REF=<tag>@<commit>`, for arg_tag_commit.
ARG_TAG_COMMIT = re.compile(
    r"^ARG [A-Z][A-Z0-9_]*=v?[0-9][0-9A-Za-z.+_-]*@[0-9a-f]{40}$"
)

SCRIPT_DIRS = ("scripts", "host")


def annotated_lines(path: Path) -> list[str]:
    """The lines that directly follow a `# renovate:` annotation."""
    lines = path.read_text().splitlines()
    return [
        line for before, line in pairwise(lines) if ANNOTATION.match(before.strip())
    ]


def scripts_with_image_pins() -> set[str]:
    """Every script under scripts/ and host/ with an annotated image pin."""
    found = set()
    for directory in SCRIPT_DIRS:
        for path in sorted((REPO_ROOT / directory).iterdir()):
            if not path.is_file():
                continue
            try:
                lines = annotated_lines(path)
            except UnicodeDecodeError:
                continue
            if any(LOOSE_IMAGE_ASSIGNMENT.match(line) for line in lines):
                found.add(str(path.relative_to(REPO_ROOT)))
    return found


def rule_grammars(path: str) -> list[str]:
    """The grammar names of the first rule matching path, as Pin Only picks it."""
    for rule in CONFIG["rules"]:
        match = rule["path"]
        if (
            (match.endswith("/") and path.startswith(match))
            or (match.startswith("*") and path.endswith(match[1:]))
            or path == match
        ):
            return [g if isinstance(g, str) else g["name"] for g in rule["grammars"]]
    return []


def arg_source_annotations(path: str) -> list[str]:
    return [
        annotation
        for source in CONFIG.get("arg_sources", [])
        if source["file"] == path
        for annotation in source["annotations"]
    ]


def test_there_are_script_pins_to_check() -> None:
    # Guards the discovery below: an empty set would pass every test vacuously.
    assert scripts_with_image_pins() >= {
        "scripts/build-images.sh",
        "host/workbench",
        "host/scrub",
    }


def test_every_script_pin_is_graded_by_shell_image_pin() -> None:
    for path in sorted(scripts_with_image_pins()):
        assert path in CONFIG["allowed_paths"], f"{path} is not in allowed_paths"
        assert rule_grammars(path) == ["shell_image_pin"], (
            f"{path} is not graded by shell_image_pin alone"
        )
        assert r"^#\s*renovate:" in arg_source_annotations(path), (
            f"{path} is not an arg_sources file with the renovate annotation"
        )


def test_every_script_pin_has_the_shape_the_grammar_reads() -> None:
    for path in sorted(scripts_with_image_pins()):
        for line in annotated_lines(REPO_ROOT / path):
            if LOOSE_IMAGE_ASSIGNMENT.match(line):
                assert SHELL_IMAGE_PIN.match(line), (
                    f"{path}: {line!r} is an image pin shell_image_pin cannot read"
                )


def test_shell_image_pin_grades_only_files_that_hold_one() -> None:
    graded = {
        rule["path"]
        for rule in CONFIG["rules"]
        if "shell_image_pin"
        in [g if isinstance(g, str) else g["name"] for g in rule["grammars"]]
    }
    assert graded == scripts_with_image_pins()


def test_agent_policy_is_graded_by_arg_tag_commit() -> None:
    dockerfile = "images/workbench/Dockerfile"
    assert "arg_tag_commit" in rule_grammars(dockerfile)
    assert r"^#\s*renovate:" in arg_source_annotations(dockerfile)
    refs = [
        line
        for line in annotated_lines(REPO_ROOT / dockerfile)
        if line.startswith("ARG AGENT_POLICY_REF=")
    ]
    assert len(refs) == 1
    assert ARG_TAG_COMMIT.match(refs[0])
