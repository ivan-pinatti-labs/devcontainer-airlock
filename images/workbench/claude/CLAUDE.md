# The workbench

You are running in a devcontainer-airlock workbench, a container, not on the
host. What that means for the commands you run (docs/LAYERS.md in
devcontainer-airlock has the full picture):

- Commands that run project code (language runtimes, package managers, test
  runners, pre-commit, scripts from the working tree) are rewritten by a
  managed hook to run in L2: a throwaway container with no network, no
  credentials, and only the working tree mounted. When a tool is "not found"
  there, the repository's L2 image lacks it (`.devcontainer/l2/Dockerfile`);
  it says nothing about the host or the workbench.
- `l2 --net -- ...` gives an L2 run the egress proxy, for installs.
  `l2 --engine -- ...` is for test suites that build or start containers.
- `gh` works through a broker that holds the token and runs an allowlist of
  commands; interactive prompts are not available, so pass the flags a
  prompt would ask for. A refusal names the allowlist.
- `git push` works over ssh through an agent that holds the key.
- `git`, `gh` and `ssh-add -l` reach the broker, the agent and GitHub only
  outside Claude Code's command sandbox, which a call leaves only when every
  part of it is an excluded command. `cd` into the repository in a call of
  its own, then run `git push` or `gh pr checks 12` by itself: no
  `cd ... &&`, no pipe into another tool, and no `git -C` or `git -c`, which
  do not match at all. Those stay inside the sandbox, where the sockets are
  blocked and `git` goes to `github.com:22` through the sandbox's own proxy
  and fails with "Bad Gateway".
- A `Permission denied (publickey)` from `git` is GitHub refusing
  authentication. Run `ssh-add -l`: "no identities" means the agent holds
  no key, so ask the user to run `make unlock` on the host. When it lists a
  key, GitHub refused that key (not added to the account, or removed), which
  the user has to look into; unlocking again will not help.
- The pull request flow is yours to run: push the branch, open it with
  `gh pr create --draft`, follow `gh pr checks`, and `gh pr ready` once they
  are green (that starts the CodeRabbit review). Answer the review, then
  enqueue the merge yourself with `gh pr merge --auto` once it is approved
  and every thread is resolved; never `--admin`, which skips the queue.
- Network access leaves through an egress proxy that allows only the egress
  sets this repository lists in `.devcontainer/egress-sets`. A "403
  Forbidden" from the proxy means the host is in none of them; say so, and
  name the set it would belong to, rather than working around it.
- Your settings and logins here are separate from anything on the host.
