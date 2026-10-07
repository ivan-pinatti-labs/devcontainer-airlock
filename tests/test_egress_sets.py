"""The egress sets as shipped in images/egress-proxy/sets/.

Each destination here was added for a measured need (issue 111), so taking
one out breaks something that worked: a Docker Hub pull, an ECR Public pull,
govulncheck's default database, or reading an aports issue.
"""

# cspell:words dualstack glxqk uabbnd
from __future__ import annotations

import tomllib

import pytest
from conftest import REPO_ROOT

SETS = REPO_ROOT / "images/egress-proxy/sets"


def domains(name):
    with open(SETS / f"{name}.toml", "rb") as handle:
        return tomllib.load(handle)["domains"]


@pytest.mark.parametrize(
    ("name", "domain"),
    [
        # Docker Hub's blob downloads redirect here (measured 2026-10-07).
        ("docker-hub", "docker-images-prod.s3.dualstack.us-east-1.amazonaws.com"),
        ("aws", "public.ecr.aws"),
        # ECR Public's blob downloads redirect here (measured 2026-10-07).
        ("aws", "d2glxqk2uabbnd.cloudfront.net"),
        ("golang", "vuln.go.dev"),
        ("alpine", "gitlab.alpinelinux.org"),
    ],
)
def test_the_set_holds_the_destination(name, domain):
    assert domain in domains(name)
