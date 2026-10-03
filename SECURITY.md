# Security Policy

## Supported Versions

This is a template repository, so there is no released version of its own
to support. Once a project is created from this template, replace this
section with the versions of that project that receive security fixes, for
example:

| Version | Supported |
| ------- | --------- |
| 1.x     | Yes       |
| < 1.0   | No        |

## Reporting a Vulnerability

Please do not open a public issue for a security vulnerability. Instead,
use GitHub's private
[report a vulnerability](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
feature on this repository, if enabled, or contact the maintainer listed in
[.github/CODEOWNERS](.github/CODEOWNERS) through their GitHub profile.

Please include as much detail as possible: steps to reproduce, affected
versions, and the potential impact. Expect an initial response within a
reasonable time, though as an individually maintained project there is no
guaranteed response window.

## What scans what

| Code | Scanned by | Where |
| --- | --- | --- |
| `.github/workflows/*` | actionlint, zizmor | `checklist-github-actions`, every commit |
| The image definitions (`images/*/Dockerfile`) | hadolint | `checklist-dev-docker`, every commit |
| Shell | shellcheck, shfmt, shebang checks | `checklist-dev-shell`, every commit |
| Everything | detect-secrets | `checklist-security-credentials`, every commit |
| The built images | Trivy, critical and high vulnerabilities, reported to code scanning | `build-images.yml`, on pull requests and pushes to `main` that change the images, and weekly |
| Everything SonarQube Cloud has an analyzer for: shell, Python, JavaScript, the Dockerfiles, YAML, `.github/workflows/*`, secrets | SonarQube Cloud, Sonar way quality gate, plus 100% coverage from `make coverage` | `sonarqube.yml`, every pull request from a branch of this repository and every push to `main` |

Two layers, deliberately. The pre-commit hooks fail before anything is
pushed; SonarQube Cloud reads the whole repository at once on every pull
request from a branch of this repository (a fork's pull request cannot
receive its token, so a maintainer pushes the branch here first). Neither
replaces the other: Sonar's rules are different from the hooks' rules, not
a superset of them.

SonarQube Cloud replaced CodeQL here, both `codeql.yml` and the GitHub
managed Code Quality setup. `codeql.yml` only ever analyzed the GitHub
Actions workflows, since CodeQL cannot read shell or a Dockerfile at all,
and the workflows are now covered by SonarQube Cloud next to actionlint and
zizmor. CodeQL's old alerts in the Security tab stop updating; they are
history, not current findings. Trivy's image reports still go to code
scanning, through `github/codeql-action/upload-sarif`, which uploads a
report and runs no CodeQL analysis.

The quality gate is the Free plan's built in "Sonar way", which cannot be
edited. It fails when new code is rated below A for reliability, security
or maintainability, when a new security hotspot is left unreviewed, when
less than 80% of new code is covered, or when more than 3% of it is
duplicated. On a change of fewer than 20 new lines, SonarQube Cloud skips
the coverage and duplication conditions. This repository holds its own code
above that floor: `make coverage`, run by the same job before the scan,
requires 100% of the lines and branches of the Python and the JavaScript
and 100% of the lines of the listed shell scripts, hands SonarQube the
reports, and fails the job otherwise, small change or not. Editing a line
makes it new code, so an old finding on that line counts against the pull
request. Fix what a rule asks for, or mark the single finding false
positive or accepted in SonarQube Cloud with the reason; no `# NOSONAR`
comments.
