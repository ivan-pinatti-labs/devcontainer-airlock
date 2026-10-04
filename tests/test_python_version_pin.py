"""Keep every copy of "which Python this repository runs" in step.

The same fact is written down in several places, and nothing derives one
from another:

- `python-version` in .github/workflows/pull-request.yml, the interpreter
  actions/setup-python gives the Pre-commit job (and so the hooks).
- `sonar.python.version` in sonar-project.properties, the version SonarQube
  Cloud's Python rules judge the code against.
- every `python:3.X-...` image the Makefile pins, which is where
  `make coverage` runs the tests.
- ruff's `target-version`, if a ruff configuration is ever added, which
  decides which idioms ruff rewrites to and which rules fire.

Nothing watches these automatically. Renovate's github-actions manager would
propose `uses-with` bumps of the workflow value, and .github/renovate.json5
disables that depType on purpose, because a `with:` input is not a pin
position the shared pin-only check can grade, so the pull request could
never merge. Renovate moves the Makefile images by digest only. Every future
Python bump is therefore a hand edit of several files, which is precisely
the kind of pairing a person forgets. This test is the reminder.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/pull-request.yml"
SONAR_PROPERTIES = REPO_ROOT / "sonar-project.properties"
MAKEFILE = REPO_ROOT / "Makefile"
RUFF_CONFIGS = [
    REPO_ROOT / name for name in ("ruff.toml", ".ruff.toml", "pyproject.toml")
]

# `python-version: "3.14"`, quoted, as actions/setup-python is given it. The
# quotes are not optional: YAML reads a bare 3.10 as the float 3.1, so an
# unquoted value is a bug worth failing on rather than a spelling to accept.
WORKFLOW_PYTHON = re.compile(
    r'^\s*python-version:\s*"(?P<major>\d+)\.(?P<minor>\d+)"\s*$', re.MULTILINE
)

# `sonar.python.version=3.14`.
SONAR_PYTHON = re.compile(
    r"^sonar\.python\.version=(?P<major>\d+)\.(?P<minor>\d+)\s*$", re.MULTILINE
)

# Any python image the Makefile pins, `.../python:3.14-slim@sha256:...` or a
# `3.14-trixie` one alike.
MAKEFILE_PYTHON = re.compile(
    r"/python:(?P<major>\d+)\.(?P<minor>\d+)-[\w.-]+@sha256:[0-9a-f]{64}"
)

# `target-version = "py314"`, in a ruff.toml or a pyproject.toml.
RUFF_TARGET = re.compile(
    r'^target-version\s*=\s*"py(?P<major>\d)(?P<minor>\d+)"\s*$', re.MULTILINE
)


def ci_python() -> tuple[str, str]:
    match = WORKFLOW_PYTHON.search(WORKFLOW.read_text())
    assert match, f"no quoted python-version found in {WORKFLOW.name}"
    return match.group("major"), match.group("minor")


def test_workflow_declares_exactly_one_python_version():
    """More than one would make "the interpreter CI uses" ambiguous."""
    matches = WORKFLOW_PYTHON.findall(WORKFLOW.read_text())
    assert len(matches) == 1, (
        f"expected exactly one quoted python-version in {WORKFLOW.name}, "
        f"found {len(matches)}: {matches}"
    )


def test_sonar_python_version_matches_the_interpreter_ci_runs():
    ci = ci_python()
    sonar = SONAR_PYTHON.search(SONAR_PROPERTIES.read_text())
    assert sonar, f"no sonar.python.version found in {SONAR_PROPERTIES.name}"

    analyzed = (sonar.group("major"), sonar.group("minor"))
    assert analyzed == ci, (
        f"{SONAR_PROPERTIES.name} analyzes as Python {'.'.join(analyzed)} "
        f"but {WORKFLOW.name} runs Python {'.'.join(ci)}. Both have to move "
        "together; nothing derives one from the other."
    )


def test_makefile_python_images_match_the_interpreter_ci_runs():
    ci = ci_python()
    images = MAKEFILE_PYTHON.findall(MAKEFILE.read_text())
    assert images, f"no digest pinned python image found in {MAKEFILE.name}"

    drifted = [image for image in images if image != ci]
    assert not drifted, (
        f"{MAKEFILE.name} pins python images for "
        f"{sorted({'.'.join(image) for image in drifted})} but {WORKFLOW.name} "
        f"runs Python {'.'.join(ci)}. They have to move together; Renovate "
        "only moves the digest, never the tag."
    )


def test_ruff_target_matches_the_interpreter_ci_runs():
    """No ruff configuration exists today, so ruff runs on its defaults. The
    moment one sets a target-version, it has to agree with CI."""
    ci = ci_python()
    for config in RUFF_CONFIGS:
        if not config.is_file():
            continue
        ruff = RUFF_TARGET.search(config.read_text())
        if ruff is None:
            continue
        target = (ruff.group("major"), ruff.group("minor"))
        assert target == ci, (
            f"{config.name} targets py{''.join(target)} but {WORKFLOW.name} "
            f"runs Python {'.'.join(ci)}. Both have to move together; "
            "nothing derives one from the other."
        )
