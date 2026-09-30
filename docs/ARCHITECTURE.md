# How the pieces fit

One picture of devcontainer-airlock: what runs inside what, how the pieces
talk to each other, and how anything reaches the internet. It is the map;
[LAYERS.md](LAYERS.md) is the reference behind it (why each line is where it
is, what was measured, and the daily routine), and [IMAGES.md](IMAGES.md)
covers how the images are built.

<!-- cspell:words pipewire seccomp -->

![The host holds two shared helpers (the ssh-agent and the gh broker) and, per workspace, an egress proxy, one workbench per agent and an L2 engine that starts L2 containers. Workbenches reach the helpers only by unix socket and the internet only through the proxy.](architecture.svg)

Reading the picture:

- **Boxes inside boxes** are containers started by the one around them, or
  grouped by what they are shared with: the host starts everything, the
  helpers serve every workspace, and each workspace has its own network,
  workbenches and engine. The egress proxy is drawn where a workspace meets
  it, but there is one for the host, on every workspace's network.
- **Amber** marks what holds a credential and the unix sockets that lead to
  it. A dashed amber line is optional: the microphone pipes of a voice
  session.
- **Green** is the network. Inside a workspace there is one internal network
  with no route out; the proxy is the only thing on it that also has one.
- **Violet dashed** is Claude Code's own command sandbox, an extra layer
  inside the workbench. It is policy, not the boundary.

## The pieces

| Piece | How many | Runs | Holds | Network |
| --- | --- | --- | --- | --- |
| host | one | podman, the VS Code window, PipeWire (the microphone, for voice) | the ssh key file, the GitHub token (a podman secret), your own logins | all of it, which is why nothing else runs here |
| ssh-agent | one per host | `ssh-agent`, read only, no capabilities | the ssh key, in memory for eight hours after `make unlock` | none |
| gh-broker | one per host | `gh`, for the commands in its allowlist | the GitHub token | straight to GitHub |
| egress-proxy | one per host | squid | nothing | every workspace's internal network, at `.2` on each, and out for the hosts of each workspace's egress sets |
| workbench | one per agent and login, per workspace | the agent, the VS Code server and extensions, git, a `gh` client of the broker | that agent's own login | the internal network only |
| L2 engine | one per workspace | rootless podman | nothing | the internal network only |
| L2 run | one per command, thrown away | hooks, tests, package installs, throwaway binaries | nothing | none (the proxy with `l2 --net`) |

A workspace is a repository, one of its worktrees, or a folder of clones
worked on together ([LAYERS.md](LAYERS.md), "Using it").

## How they talk to each other

The egress proxy is the one piece listening on a network port: 8888, on
each workspace's internal network, for outbound traffic. It tells the
workspaces apart by the network a request comes from. Everything else local is
a unix socket in a folder the host creates and mounts only where it belongs,
and SELinux lets these containers connect only to a socket held by one in
their own domain and category. Voice is not a socket either: two named
pipes, one carrying microphone audio in and one carrying `start` and `stop`
out.

| From | To | Over | Carries |
| --- | --- | --- | --- |
| workbench (`gh`) | gh-broker | `gh.sock` | a `gh` command line; the broker checks it against its allowlist and the owners, runs it with the token, and sends back the output |
| workbench (`git`, `ssh`) | ssh-agent | `agent.sock` | signing requests; the key never leaves the agent |
| workbench (`l2`, `podman`) | L2 engine | `podman.sock` | the podman API: start an L2 run, build an L2 image |
| L2 engine | L2 run | inside the engine | the run gets the working tree, and nothing of the workbench (not its processes, not its localhost) |
| PipeWire on the host | workbench | a named pipe | microphone audio for a session with voice (`make claude`), only while Claude Code records ([LAYERS.md](LAYERS.md), "Voice") |
| workbench (`rec`) | `host/workbench` | a second named pipe | the words `start` and `stop`, nothing else understood; they switch the microphone on and off |
| VS Code window | workbench | podman, from the host | the editor attaches to the running container; its server and extensions run there |

What is deliberately missing: L2 has no socket to the broker, the agent or
the engine unless a run asks for the engine (`l2 --engine`), and the
workbench has no container runtime of its own, only a client of the engine.

## How anything reaches the internet

| Who | Path | Allowed by |
| --- | --- | --- |
| an agent's own traffic (its API, the VS Code server, extensions) | workbench, then the egress proxy | the `workbench` egress set, always on |
| `git fetch`, `git push` | workbench ssh, tunnelled through the proxy to `ssh.github.com:443`, signed by the ssh-agent | the `github` egress set, always on; host keys checked strictly against GitHub's published ones |
| `gh` | workbench to the broker by socket, then the broker straight to GitHub | the broker's allowlist of commands and owners |
| package installs, tool downloads | an L2 run with `l2 --net`, or the engine pulling an image, through the proxy | the egress sets the repository lists in `.devcontainer/egress-sets` |
| everything else | the proxy | nothing: refused with a 403 |
| the proxy itself | out, to refresh GitHub's and other providers' address ranges | its own configuration |

So a workbench can reach only the always on sets and the ones its workspace
names, and
GitHub's API only through the broker's allowlist. An L2 run reaches nothing
unless it asks, and then only what the proxy allows.

## Claude Code's own sandbox

Claude Code runs every Bash call in a sandbox of its own (bubblewrap, a
seccomp filter and a proxy of its own). Inside the workbench it is an extra
layer: it blocks every unix socket and sends traffic through its own proxy,
so the commands that need a socket are excluded from it and run in the
workbench as normal, still asking before they run: `git`, `gh`,
`ssh-add -l`, `l2` and `podman`. A call leaves the sandbox only when every
part of it matches; `cd ... && git push`, a pipe into another tool, and
`git -C` or `git -c` stay inside and fail. [LAYERS.md](LAYERS.md), "The
coding agents", has the measurements. Codex keeps its own sandbox.

## What is the boundary, and what is only policy

The boundary is where things are: the token and the key are not in the
workbench, L2 holds nothing, and the only way out is the proxy. That holds
whatever an agent, an extension or a postinstall script decides to do.

The files the agents are held to (the hook that sends project code to L2,
the sandbox settings, the instructions they read) are policy. They keep a
well behaved agent on the right path, and a determined one could get around
them; that is why the boundary does not depend on them. [LAYERS.md](LAYERS.md)
says which is which, piece by piece.
