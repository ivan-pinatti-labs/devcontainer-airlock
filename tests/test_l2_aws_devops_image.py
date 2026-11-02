"""The AWS L2 image's Dockerfile keeps every tool pinned and version checked.

images/l2-aws-devops/Dockerfile ends with a build step that runs every tool
with no network and makes each pinned one report the version its pin names.
That only protects the tools the step lists. A tool copied out of a new image
stage, or a new release argument, that the step does not name would build
and publish without anything checking it runs, and a pin Renovate cannot see
would never move. These tests say so before a build does.
"""

from __future__ import annotations

import re
from itertools import pairwise

import yaml
from conftest import REPO_ROOT

DOCKERFILE = REPO_ROOT / "images/l2-aws-devops/Dockerfile"
TEXT = DOCKERFILE.read_text()
LINES = TEXT.splitlines()

# An upstream image stage: pinned by tag and digest, named after its tool.
IMAGE_STAGE = re.compile(
    r"^FROM (?P<image>[a-z0-9.-]+(?:/[a-z0-9._-]+)+):(?P<tag>[A-Za-z0-9._-]+)"
    r"@sha256:[0-9a-f]{64} AS (?P<stage>[a-z0-9-]+)$"
)
VERSION_ARG = re.compile(r"^ARG (?P<name>[A-Z0-9_]+_VERSION)=(?P<value>\S+)$")
ANNOTATION = re.compile(
    r"^# renovate: datasource=(?P<datasource>\S+) depName=(?P<dep>\S+)$"
)


def image_stages() -> dict[str, str]:
    """Each upstream image stage's name, and its tag."""
    found = {}
    for line in LINES:
        if line.startswith("FROM ") and " AS " in line and "${" not in line:
            match = IMAGE_STAGE.match(line)
            assert match, f"{line!r} is not an image pinned by tag and digest"
            found[match["stage"]] = match["tag"]
    return found


def version_args() -> dict[str, str]:
    """Each _VERSION argument with a default, and the annotation above it."""
    found = {}
    for before, line in pairwise(LINES):
        match = VERSION_ARG.match(line)
        if match:
            annotation = ANNOTATION.match(before)
            assert annotation, f"{line!r} has no renovate annotation above it"
            found[match["name"]] = annotation["datasource"]
    return found


def test_there_are_tools_to_check() -> None:
    # Guards the tests below, which would pass on an empty file.
    assert set(image_stages()) >= {"atmos", "tflint", "kubectl", "yq"}
    assert set(version_args()) == {
        "HELM_VERSION",
        "EKSCTL_VERSION",
        "TERRAFORM_VERSION",
        "AWSCLI_VERSION",
    }


def test_every_image_stage_is_copied_out_and_version_checked() -> None:
    for stage in image_stages():
        assert f"COPY --from={stage} " in TEXT, f"nothing is copied out of {stage}"
        assert f'reports "$(tag {stage})" ' in TEXT, (
            f"{stage} is not in the version check"
        )


def test_every_release_is_annotated_declared_once_and_version_checked() -> None:
    for name, datasource in version_args().items():
        assert datasource in {"github-releases", "github-tags"}
        # A default given twice would leave the second one unmoved by
        # Renovate, and the version check would then refuse every bump.
        assert TEXT.count(f"ARG {name}=") == 1
        assert f'reports "${{{name}}}" ' in TEXT, f"{name} is not version checked"


def test_releases_are_declared_before_any_stage() -> None:
    first_from = next(i for i, line in enumerate(LINES) if line.startswith("FROM "))
    for i, line in enumerate(LINES):
        if VERSION_ARG.match(line):
            assert i < first_from, f"{line!r} is declared inside a stage"


def test_the_version_check_runs_without_network() -> None:
    checks = [line for line in LINES if 'reports "' in line]
    assert checks
    step = TEXT[: TEXT.index(checks[0])].rsplit("\nRUN ", 1)[1]
    assert step.startswith("--network=none ")


def test_built_on_the_l2_of_the_same_run() -> None:
    script = (REPO_ROOT / "scripts/build-images.sh").read_text()
    assert re.search(r"^ON_L2=\(.*\bl2-aws-devops\b.*\)$", script, re.MULTILINE)
    assert "ARG L2_IMAGE=localhost/airlock-l2:local" in LINES
    workbench = (REPO_ROOT / "host/workbench").read_text()
    assert (
        '--build-arg "L2_IMAGE=localhost/airlock-l2:${TAG}" '
        '-t "localhost/airlock-l2-aws-devops:${TAG}"'
    ) in workbench


def test_pin_only_grades_its_pins() -> None:
    config = yaml.safe_load((REPO_ROOT / ".github/pin-only.yml").read_text())
    path = "images/l2-aws-devops/Dockerfile"
    assert path in config["allowed_paths"]
    assert any(source["file"] == path for source in config["arg_sources"])
    # The vendored key is reviewed by a person, never moved by a bot.
    assert not any("keyrings" in allowed for allowed in config["allowed_paths"])


def test_renovate_holds_every_pin_but_tflint() -> None:
    # The pins follow a consuming infrastructure repository and are bumped by
    # hand. The rule is read as text: the test environment has no json5
    # parser, and the rule's shape is what matters here.
    config = (REPO_ROOT / ".github/renovate.json5").read_text()
    rule = config[config.index('matchFileNames: ["images/l2-aws-devops/Dockerfile"]') :]
    rule = rule[: rule.index("}")]
    assert 'matchPackageNames: ["!ghcr.io/terraform-linters/tflint"]' in rule
    assert "enabled: false" in rule
    assert "ghcr.io/terraform-linters/tflint" in image_refs()


def image_refs() -> set[str]:
    """The upstream images the stages pin."""
    return {match["image"] for line in LINES if (match := IMAGE_STAGE.match(line))}
