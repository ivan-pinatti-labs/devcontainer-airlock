# Where the tools come from

<!-- cspell:words nodesource keyrings dearmor dearmored enarmor gpgv -->
<!-- cspell:words dpkg nodistro pkgs pipx subkey subkeys userns SLSA -->

Every command line tool these repositories develop against, where it is
installed from, and what actually vouches for it.

## Why there is no version manager

asdf was removed from this organization on 2026-09-19. It installed tools by
running a plugin, and a plugin is a git repository of shell scripts that
downloads a release and puts it on PATH. Pinning the plugin to a reviewed
commit, which this organization did, fixes which scripts run; it does not
give the downloaded tool a signature, a build attestation or a maintainer
who ships security fixes for it. Nor does the image scanner see any of it: a
tool unpacked into a home directory carries no package metadata, so Trivy
reported nothing about the entire toolchain.

mise and aqua were considered and rejected. They are better version managers
than asdf, but the trust model is the same one: a manifest in a repository
somewhere decides what gets downloaded and executed.

Packages replaced it. A package comes from a repository whose index is
signed, is unpacked by apt rather than by a script, records itself in the
dpkg database where the scanner can see it, and has somebody upstream
issuing security updates for it.

## No version pins

Nothing here pins a package version, deliberately.

Ubuntu and the third party repositories below all ship security fixes by
moving a package's version inside a release. Pinning would hold an image on
the superseded build until somebody edited the pin by hand, which is the
opposite of what the scheduled rebuild exists for.

The digest pins what an image builds *on*, not what apt resolves on top, so
two builds of the same Dockerfile weeks apart can and should install
different package builds. This layer is deliberately rolling, not
reproducible, and the two should not be confused: reproducing an exact
container means keeping the image, not re-running the build. Rebuilding is
what makes it current, which is the property being chosen here.

What that costs is the seven day cooling window Renovate applies elsewhere:
there is no version for it to hold back. What replaces it is the
distribution's own review, and the fact that a rebuild is published as a new
digest that nothing consumes until a repository bumps its pin, with CI
building the development container and exercising its tools before that
bump can merge.

## The keys

The base image carries three third party apt signing keys, vendored under
`images/base/keyrings/`. Each file's set of **primary** certificates is
compared against this list at build time, exactly: every fingerprint here
must be present and no other may be.

| Keyring | Primary certificates | Signs |
| --- | --- | --- |
| `github-cli.gpg` | `2C6106201985B60E6C7AC87323F3D4EA75716059`, `7F38BBB59D064DBCB3D84D725612B36462313325` | GitHub CLI |
| `nodesource.gpg` | `6F71F525282841EEDAF851B42F59B5F99B1BE0B4` | NodeSource Node.js |
| `hashicorp.gpg` | `D55C0D1AC78A8D8126CB631CFC9CA96ACA026560` | HashiCorp Terraform |

github-cli carries two because GitHub rotates by publishing the new
certificate alongside the old one. Pinning only one of them would fail
verification the day they switch, which is why the check is an exact set
rather than a single value.

Subkeys are deliberately not listed. A subkey is bound to its primary by a
signature gpg already verifies, so naming the primaries fixes the whole
trust chain, and listing subkeys as well would mean editing this file every
time a vendor rotated a signing subkey under an unchanged primary.

An exact set, rather than "the expected fingerprint appears somewhere in the
file", because `gpg --dearmor` installs every certificate a file holds and
apt then accepts repository metadata signed by any of them. A vendor quietly
adding a second certificate, or a tampered file with one appended, would
otherwise install a signer nobody reviewed while the check still passed.
CodeRabbit caught precisely that on the first version of this change.

They are vendored rather than downloaded during the build so that a key
changing is a diff somebody reviews in a pull request, not whatever the
vendor served on the day of the build. Three failure modes were confirmed
against a real build: replacing a key with a freshly generated one, adding a
rogue certificate to an otherwise correct file, and removing one of the two
reviewed GitHub certificates. All three stop the build.

**Installing a key grants nothing on its own.** apt reads a keyring only when
a `sources.list` entry names it with `signed-by=`. The base image ships the
keys and enables none of the repositories. An image that wants one of these
tools adds its own source entry (the workbench enables NodeSource, the gh
broker GitHub's, a repository's L2 image HashiCorp's).

## Enabling a repository

In that repository's `.devcontainer/Dockerfile`, as root, before installing:

```dockerfile
RUN printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/github-cli.gpg] https://cli.github.com/packages stable main' \
      > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends gh \
  && rm -rf /var/lib/apt/lists/*
```

The other two source lines, where they are needed:

```text
deb [arch=amd64 signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main
deb [arch=amd64 signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com resolute main
```

`resolute` is Ubuntu 26.04's codename, which HashiCorp publishes a component
for. A base image moving to a new Ubuntu release has to check that the
component exists before the move, because apt fails closed on a missing one
rather than falling back.

`Pin Only` does not allow a dependency bot to touch any of these lines, or
any keyring file. Only a version and a digest normalize away; a source URL,
a keyring path or a package name changing has no counterpart on the other
side of the diff and the pull request waits for a person.

## Every tool

Which image each tool is in follows from the layers ([LAYERS.md](LAYERS.md)):
credentials and agents in the workbench, project tooling in L2.

| Tool | Source | Provenance | Where |
| --- | --- | --- | --- |
| git, curl, jq, python3, procps | Ubuntu | distribution repository signature | base |
| podman, crun, catatonit, fuse-overlayfs, passt, nftables, uidmap, libcap2-bin, podman-compose | Ubuntu | distribution repository signature | L2 engine |
| podman-remote | Ubuntu | distribution repository signature | workbench (as `podman`) and L2, clients of the engine only |
| pre-commit | Ubuntu | distribution repository signature | L2; 4.5.1 on 26.04 |
| shellcheck, golang-go, make | Ubuntu | distribution repository signature | L2 (make also in the workbench) |
| bubblewrap, socat, ripgrep, openssh-client | Ubuntu | distribution repository signature | workbench |
| sox | Ubuntu | distribution repository signature | Claude workbench (voice mode records with SoX) |
| squid | Ubuntu | distribution repository signature | egress proxy |
| github-cli | `cli.github.com/packages stable main` | repository signature | gh broker only; the workbench's `gh` is a shim that asks the broker |
| nodejs | `deb.nodesource.com/node_24.x nodistro main` | repository signature | workbench (the agent CLIs run on it) and L2 (node hooks) |
| terraform | `apt.releases.hashicorp.com resolute main` | repository signature | the L2 image of the repository that uses it |
| hadolint, actionlint, dotenv-linter | the projects' official images, pinned by digest | container image published by the project | L2, copied out of those images, because L2 cannot start containers |
| Go modules for golang hooks (checkmake, gitleaks) | the Go module proxy, at the versions in `images/l2/go-modules.txt` | Go's checksum database, checked when the image is built; the image digest pins the result | L2, a read only module proxy; the hooks still build from source there |
| claude (Claude Code) | npm `@anthropic-ai/claude-code`, version pinned | npm registry signature only, **no build provenance** | workbench, see below |
| codex (Codex CLI) | npm `@openai/codex`, version pinned | npm registry signature **and** SLSA build provenance | workbench, see below |
| VS Code extensions | the Visual Studio Marketplace, version pinned | Marketplace signature, checked on install | workbench, read only; `images/workbench/vscode/extensions.txt` |

### tflint is the exception

tflint publishes no apt repository, from its own project or from any
distribution. The remaining options were a release download checked against a
checksum, which is what asdf was already doing and is the thing being
retired, or the project's own container image.

It runs from the image. The base image already carries rootless Podman, so a
wrapper on PATH runs the pinned image and the `gruntwork-io/pre-commit`
`tflint` hook, which invokes whatever `tflint` it finds, needs no change.

The honest limitation: a tool that runs from its own image is not installed
in this organization's images, so it does not appear in their SBOMs and is
not covered by their vulnerability scans. It is scanned by whoever publishes
it, not here. That is a real reduction in visibility, accepted because the
alternative kept an unsigned release download in the build.

### The two coding agent CLIs

Neither Claude Code nor Codex publishes an apt repository, from its vendor or
from any distribution, so neither can follow the rule the rest of this
document sets out. npm is the vendors' own distribution channel for both, and
unlike a bare download it can actually be verified, so that is what the build
does and what it checks.

`npm audit signatures` runs as a build step and the build fails closed on it.
It checks two different things, and the two packages do not both get both:

| | Claude Code | Codex |
| --- | --- | --- |
| npm registry signature | yes | yes |
| SLSA build provenance attestation | **no** | yes |

The registry signature means the bytes installed are the bytes the publisher
uploaded, verifiable against npm's own public key. The SLSA attestation goes
further and ties the tarball to the source commit and the workflow that built
it. Measured 2026-09-19 against the registry: `@openai/codex` publishes one
and `@anthropic-ai/claude-code` does not. "Installed from npm" is therefore a
weaker statement for Claude Code than for Codex, and this table exists so
that difference is visible rather than implied.

Nothing is executed before it is verified. npm runs a package's lifecycle
scripts during install by default, as root in a build like this one, and it
does that before `npm audit signatures` has had any chance to say whether
the tarball is genuine. A tampered package would run first and be rejected
afterwards, which is the wrong way round. So the build installs with
`--ignore-scripts`, verifies, and only then runs the postinstall of a
package that has just passed.

Only Claude Code needs that postinstall. Codex declares no lifecycle scripts
at all. Claude Code's does not download anything either, which is worth
stating because a wrapper package that fetches a binary at install time
would put that binary outside everything described above: the native
executable ships as a platform specific `optionalDependency`
(`@anthropic-ai/claude-code-linux-x64`), which npm installs and
`npm audit signatures` covers like any other package, and the postinstall
only selects and links it.

These two are **version pinned**, which nothing installed with apt is. The
difference is deliberate. An apt package is unpinned because Ubuntu and these
vendors ship security fixes by moving a version inside a release, with a
distribution maintainer sitting between upstream and this image. npm has no
such gatekeeper: whatever a publisher pushes is what `npm install` resolves
seconds later. The pin is what lets Renovate's seven day `minimumReleaseAge`
and `Pin Only` stand between a fresh npm release and this image, and it is
the only defence that works against a release that is well formed and
malicious.

That window covers updates Renovate proposes, and nothing else. A version
typed into the Dockerfile by hand never passes through it, so an initial pin
added on the day it was published would walk straight past the control the
pin exists to enable. Initial values are therefore chosen to be at least
seven days old when they are introduced, and the same applies to anyone
changing one by hand rather than letting Renovate do it.

## Getting a shell without an IDE

`host/workbench up` starts the workbench and its helpers, and
`host/workbench shell` is a terminal in it, so nothing here requires an
editor; [LAYERS.md](LAYERS.md) has the daily routine.

The agents' logins and settings live in `~/.local/share/workbench/claude`
and `codex` on the host, mounted into the workbench. They are deliberately
not your own `~/.claude` and `~/.codex`. An earlier version of this page
mounted those read write, which let anything running in the development
container (a pre-commit hook, an npm postinstall script) edit the settings
the agents on the host read, hooks included: code execution on the host by
another name. The price is logging the agents in once more, inside the
workbench.

## What the scanners see now

Measured 2026-09-19 against a development container built on this base,
with Trivy 0.74.0:

- `gh`, `nodejs`, `terraform`, `pre-commit` and `shellcheck` all appear as
  `os-pkgs` entries. Under asdf, none of them appeared at all.
- Trivy additionally unpacks the Go binaries behind `gh` and `terraform` and
  the Node.js module tree, reporting 16 vulnerabilities in their bundled
  dependencies that were previously invisible.
- Python packages installed with pip, pipx or into a virtual environment are
  detected too, as `lang-pkgs` / `python-pkg`, so the test dependency
  environments in the sibling repositories are covered.

---

See also: [IMAGES.md](IMAGES.md)
