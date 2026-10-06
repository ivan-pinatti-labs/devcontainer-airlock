# devcontainer-airlock

[![License](https://img.shields.io/github/license/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](LICENSE.md)
[![GitHub issues](https://img.shields.io/github/issues-raw/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](https://github.com/ivan-pinatti-labs/devcontainer-airlock/issues)
[![GitHub Sponsors](https://img.shields.io/github/sponsors/ivan-pinatti?logo=Github&style=for-the-badge)](https://github.com/sponsors/ivan-pinatti)
[![GitHub Repo stars](https://img.shields.io/github/stars/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](https://github.com/ivan-pinatti-labs/devcontainer-airlock)
[![GitHub forks](https://img.shields.io/github/forks/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](https://github.com/ivan-pinatti-labs/devcontainer-airlock/forks)
[![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/ivan-pinatti-labs/devcontainer-airlock?utm_source=oss&utm_medium=github&utm_campaign=ivan-pinatti-labs%2Fdevcontainer-airlock&labelColor=171717&color=FF570A&label=CodeRabbit+Reviews&style=for-the-badge)](https://coderabbit.ai)
[![SonarQube Quality Gate](https://img.shields.io/sonar/quality_gate/ivan-pinatti-labs_devcontainer-airlock?server=https%3A%2F%2Fsonarcloud.io&logo=sonarqubecloud&style=for-the-badge)](https://sonarcloud.io/project/overview?id=ivan-pinatti-labs_devcontainer-airlock)
[![SonarQube Coverage](https://img.shields.io/sonar/coverage/ivan-pinatti-labs_devcontainer-airlock?server=https%3A%2F%2Fsonarcloud.io&logo=sonarqubecloud&style=for-the-badge)](https://sonarcloud.io/component_measures?id=ivan-pinatti-labs_devcontainer-airlock&metric=coverage)

Secure, layered devcontainers for AI coding agents.

Coding agents (Claude Code, Codex) and their editor extensions are powerful
and trusted with a lot: a GitHub token, an ssh key, the network, and every
hook, test and `npm install` they run. devcontainer-airlock splits that
into layers by what each one is trusted with:

- **A workbench per agent**, where you and that agent work. It holds no
  GitHub token, no ssh key and no other agent's login, and it has no direct
  network and no container runtime.
- **L2 containers** for hooks, tests, package installs and throwaway
  binaries. They get the working tree and nothing else: no network, no
  credentials.
- **Beside them**, a GitHub broker that holds the token and runs an allowlist
  of `gh` commands, an ssh-agent that holds the key, and an egress proxy
  per workspace that allows only the services a project names.

No agent, extension or project code runs on the host: only podman, the
editor window, and for voice mode PipeWire.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the picture: what runs
inside what, how the pieces talk to each other, and how anything reaches
the internet. [docs/LAYERS.md](docs/LAYERS.md) explains the layers, says
plainly which parts are a boundary and which are only policy the agents are
asked to follow, and covers the daily routine.

"devcontainer" here means a development container, not the Dev Containers
specification: there is no `devcontainer.json`. VS Code attaches to a
running workbench (**Dev Containers: Attach to Running Container**).

## The command policy: agent-policy

[agent-policy](https://github.com/ivan-pinatti-labs/agent-policy) decides
which commands a coding agent may run on its own, which it must ask about
first, and which it must hand to you, for Claude Code and Codex alike. It
lives in a repository of its own because it also works without the airlock,
on any machine where an agent runs. The two are meant to be used together,
and that is strongly recommended: each solves a different half of the same
problem, running a coding agent and its development work more safely.

| | devcontainer-airlock | agent-policy |
| --- | --- | --- |
| Decides | what a command can reach | whether a command runs at all |
| How | containers: no credentials in the workbench, project code in L2 with no network, egress through an allowlist | rules and a guard hook the agent cannot edit: allow, ask, or refuse and hand to you |
| Stops | a command that runs from reaching the token, the ssh key, the host, or anything on the internet the project does not name | a force push, a hook bypass, reading a login, `terraform destroy`, before it runs |
| Kind of control | a boundary | policy |

Neither replaces the other. The airlock limits the harm of a command the
policy let through. The policy stops commands the airlock would let run
because they stay inside the workspace: a force push of your branch,
deleting a remote branch, a commit that skips the hooks.

**In the airlock it is already on.** The workbench images render
agent-policy at a pinned release and install its rules and its guard hook
(beside the L2 routing hook) for both agents, and its sandbox path lists for
Claude Code, the one of the two with a sandbox that takes them. A new
release reaches the images through a Renovate pull request that waits for a
person. The airlock's own additions, an overlay that can only add or harden,
live in `images/workbench/agent-policy/`;
[docs/LAYERS.md](docs/LAYERS.md), "agent-policy", has the details.

**Outside the airlock**, on a machine where an agent runs directly, install
agent-policy on its own; its README covers `make install`.

## Status

The images are published to
`ghcr.io/ivan-pinatti-labs/airlock-<name>` and are in daily use for the
`ivan-pinatti-labs` repositories. Making them easy to adopt in any project
is in progress: some settings are still specific to that organization (the
GitHub owners the broker allows, for one). Until 2026-09-26 this repository
was `devcontainer-images` and the images were
`ghcr.io/ivan-pinatti-labs/devcontainer-<name>`; those old packages are no
longer updated. [docs/IMAGES.md](docs/IMAGES.md) covers what each image
carries and how the build works.

## Requirements

- [Podman](https://podman.io/), rootless. It is the runtime these images are
  built and run with. Nothing here assumes a daemon or a mounted socket.
- An SELinux enforcing host is what this was built and measured on. Nothing
  here turns labelling off.

## Usage

```shell
host/workbench init     # once, in an existing project: egress sets, L2 image, make targets
host/workbench claude   # from a folder of clones: one workspace over all of them (docs/LAYERS.md)
make workbench-build    # every image, locally (or make workbench-pull)
make unlock             # the ssh key, for eight hours
make claude             # Claude Code in its workbench, started if needed, with voice (or: codex)
make claude-remote      # the same with remote control; claude-plain has neither
make claude-shell       # a terminal in that workbench (or: codex-shell)
```

Or attach VS Code to the running `workbench-claude-<folder>` or
`workbench-codex-<folder>` container, whichever agent's extension you want;
each agent has a workbench of its own and cannot read the other's login.
Published images are consumed by digest rather than by a floating tag, so a rebuild
cannot change what a repository builds against until someone bumps the pin.

## How images are built

`scripts/build-images.sh`, in CI and locally alike: base first, then every
other image on that exact base (the nested test runner on its own pinned
upstream image), each scanned before anything is published,
and what is published is the scanned manifest itself. A secret found in a
layer blocks the publish. Vulnerabilities are reported rather than blocking,
with one exception: a critical one carrying a fix blocks. Scheduled rebuilds
pick up upstream security fixes and publish new digests without cutting a
release.

## License

[![license](https://img.shields.io/github/license/ivan-pinatti-labs/devcontainer-airlock?style=plastic)](https://github.com/ivan-pinatti-labs/devcontainer-airlock/blob/main/LICENSE.md)

See [LICENSE](LICENSE.md) for the full terms, and [NOTICE](NOTICE.md) for
third party notices.

From the Apache License 2.0, sections 7 and 8:

> Unless required by applicable law or agreed to in writing, Licensor provides
> the Work (and each Contributor provides its Contributions) on an "AS IS"
> BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
> implied, including, without limitation, any warranties or conditions of TITLE,
> NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. You
> are solely responsible for determining the appropriateness of using or
> redistributing the Work and assume any risks associated with Your exercise of
> permissions under this License.
>
> In no event and under no legal theory, whether in tort (including
> negligence), contract, or otherwise, unless required by applicable law (such
> as deliberate and grossly negligent acts) or agreed to in writing, shall any
> Contributor be liable to You for damages, including any direct, indirect,
> special, incidental, or consequential damages of any character arising as a
> result of this License or out of the use or inability to use the Work (…),
> even if such Contributor has been advised of the possibility of such damages.

---

## Contribute / Donate

Contributions, bug reports, and feature requests are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md).

If you are using this code, forking it, or getting ideas from it, sponsorships
and donations help keep the project maintained.

<!-- markdownlint-disable MD013 MD033 -->
<!-- The badges and QR codes are HTML for their layout, and their URLs and the
     networks footnote below cannot be wrapped without breaking it. -->

<div align="center">

<a href="https://github.com/sponsors/ivan-pinatti">
  <img
  src="https://img.shields.io/badge/Sponsor-%E2%9D%A4-fe8e86?logo=github&style=for-the-badge"
  alt="GitHub Sponsor">
</a>
<a href="https://www.buymeacoffee.com/ivan.pinatti">
  <img
  src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?logo=buy-me-a-coffee&logoColor=black&style=for-the-badge"
  alt="Buy Me a Coffee">
</a>
<a href="https://www.paypal.com/paypalme/ivanrpinatti">
  <img
  src="https://img.shields.io/badge/PayPal-Donate-003087?logo=paypal&style=for-the-badge"
  alt="PayPal">
</a>

</div>

<table>
  <tr>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/btc.png"
        alt="BTC donation QR code" width="85">
      <br><code>&nbsp;BTC&nbsp;&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/eth.png"
        alt="ETH donation QR code" width="85">
      <br><code>ERC&#8209;20</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/xmr.png"
        alt="XMR donation QR code" width="85">
      <br><code>&nbsp;XMR&nbsp;&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/xrp.png"
        alt="XRP donation QR code" width="85">
      <br><code>&nbsp;XRP&nbsp;&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/ada.png"
        alt="ADA donation QR code" width="85">
      <br><code>&nbsp;ADA&nbsp;&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/atom.png"
        alt="ATOM donation QR code" width="85">
      <br><code>&nbsp;ATOM&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/bch.png"
        alt="BCH donation QR code" width="85">
      <br><code>&nbsp;BCH&nbsp;&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/bnb.png"
        alt="BNB donation QR code" width="85">
      <br><code>BEP&#8209;20</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/doge.png"
        alt="DOGE donation QR code" width="85">
      <br><code>&nbsp;DOGE&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/kava.png"
        alt="KAVA donation QR code" width="85">
      <br><code>&nbsp;KAVA&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/ltc.png"
        alt="LTC donation QR code" width="85">
      <br><code>&nbsp;LTC&nbsp;&nbsp;</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/trx.png"
        alt="TRX donation QR code" width="85">
      <br><code>TRC&#8209;20</code>
    </td>
    <td align="center">
      <img
src="https://raw.githubusercontent.com/ivan-pinatti-labs/.github/main/docs/crypto/qr-codes/zec.png"
        alt="ZEC donation QR code" width="85">
      <br><code>&nbsp;ZEC&nbsp;&nbsp;</code>
    </td>
  </tr>
</table>

_\* ERC-20 accepts ETH, USDT, and USDC · BEP-20 accepts BNB, USDT, and USDC ·
TRC-20 accepts TRX, USDT, and USDC. See the
[full list](https://github.com/ivan-pinatti-labs/.github/blob/main/docs/crypto/addresses.md)_

<!-- markdownlint-enable MD013 MD033 -->
