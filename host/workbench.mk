# make targets for the workbench, for any repository's Makefile to include:
#
#   WORKBENCH_HOME ?= <path to a devcontainer-airlock clone>
#   -include $(WORKBENCH_HOME)/host/workbench.mk
#
# Each target calls host/workbench next to this file, for the repository
# `make` runs in (its main clone, so its worktrees are included). Every target
# is named for the workbench, so none collides with a repository's own
# (`up` and `down` are often taken). `make <Tab><Tab>` lists them.
#
# host/workbench on its own starts Claude Code with neither voice nor remote
# control. These targets are the daily ones: `claude` has voice (push to
# talk, the microphone routed only while space is held) and remote control,
# and `claude-plain` has neither. `make claude VOICE=0` turns voice off for one
# run, `REMOTE=0` remote control. `claude-remote` stays for old habits.
# Codex has no voice mode.
#
# checkmake reads only the first physical line of a .PHONY declaration, so
# this one stays on one line.
.PHONY: claude codex sessions prune claude-shell codex-shell claude-remote claude-plain claude-assisted claude-unattended unlock workbench-help workbench-up workbench-down workbench-status workbench-build workbench-pull

WORKBENCH := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))/workbench
VOICE ?= 1
REMOTE ?= 1
# `make claude resume=<id>` (or RESUME=) continues that Claude Code
# conversation in a new session: the id Claude Code prints when it exits.
# host/workbench checks it is one before anything starts.
resume ?=
RESUME ?= $(resume)
# Through the environment, so no quote in it can break the recipe's shell.
export RESUME
RESUME_ARG = $(if $(RESUME),--resume "$$RESUME")
# `make claude-unattended HOURS=24` skips its menu; `make unlock HOURS=24`
# unlocks for that long instead of 12 hours (at most 48).
HOURS ?=
export HOURS
HOURS_ARG = $(if $(HOURS),--hours "$$HOURS")

# A repository's own `help` target lists these by depending on this one.
workbench-help:
	@printf '%s\n' \
		'Workbench:' \
		'  claude                      A new Claude Code session, here, with voice (hold space) and remote control (REMOTE=0: without).' \
		'                              resume=<id>: continue that conversation instead (any claude target).' \
		'  claude-remote               The same as claude, whatever REMOTE says.' \
		'  claude-plain                Claude Code with neither voice nor remote control.' \
		'  claude-assisted             Claude Code that asks less about reads, with you there (docs/MODES.md).' \
		'  claude-unattended           Claude Code with nobody to ask, for 8 to 48 hours (a menu, or HOURS=).' \
		'  codex                       A new Codex session, here.' \
		'  sessions                    Every session: running or stopped, and its clones and branches.' \
		'  attach-<session>            Resume a running or recent session (shell-, stop- for running ones).' \
		'  session-attach S=<name>     Any session by name (session-shell, -stop, -prune the same way).' \
		'  prune                       Remove stopped sessions with nothing left only in them.' \
		'  claude-shell                A terminal in the Claude workbench.' \
		'  codex-shell                 A terminal in the Codex workbench.' \
		'  unlock                      Unlock the ssh key for git push, for 12 hours (HOURS= up to 48).' \
		'  workbench-up                Start this repository workbenches and L2 engine, and the shared services.' \
		'  workbench-down              Stop them; the shared services too when nothing else uses them.' \
		'  workbench-status            What is running.' \
		'  workbench-build             Build every image locally.' \
		'  workbench-pull              Or pull the published images instead.'
	@$(foreach a,$(WORKBENCH_ACCOUNTS),printf '  %-27s %s\n' 'claude-$(a), codex-$(a)' 'The same, logged in as $(a) (-shell, and claude-$(a)-remote, -plain, -assisted, -unattended).';)

# One more set of targets per account in WORKBENCH_ACCOUNTS (claude-personal,
# codex-personal and their shells), so Tab lists them too.
WORKBENCH_ACCOUNTS := $(shell $(WORKBENCH) accounts 2>/dev/null)
define workbench_account
.PHONY: claude-$(1) codex-$(1) claude-$(1)-shell codex-$(1)-shell claude-$(1)-remote claude-$(1)-plain claude-$(1)-assisted claude-$(1)-unattended
claude-$(1):
	@WORKBENCH_VOICE=$$(VOICE) $$(WORKBENCH) session start claude-$(1) $$(if $$(filter 1,$$(REMOTE)),--remote) $$(RESUME_ARG)
codex-$(1):
	@$$(WORKBENCH) session start codex-$(1)
claude-$(1)-shell:
	@$$(WORKBENCH) shell claude-$(1)
codex-$(1)-shell:
	@$$(WORKBENCH) shell codex-$(1)
claude-$(1)-remote:
	@WORKBENCH_VOICE=$$(VOICE) $$(WORKBENCH) session start claude-$(1) --remote $$(RESUME_ARG)
claude-$(1)-plain:
	@WORKBENCH_VOICE=0 $$(WORKBENCH) session start claude-$(1) $$(RESUME_ARG)
claude-$(1)-assisted:
	@WORKBENCH_VOICE=$$(VOICE) $$(WORKBENCH) session start claude-$(1) --level assisted $$(if $$(filter 1,$$(REMOTE)),--remote) $$(RESUME_ARG)
claude-$(1)-unattended:
	@WORKBENCH_VOICE=0 $$(WORKBENCH) session start claude-$(1) --level unattended $$(if $$(filter 1,$$(REMOTE)),--remote) $$(HOURS_ARG) $$(RESUME_ARG)
endef
$(foreach a,$(WORKBENCH_ACCOUNTS),$(eval $(call workbench_account,$(a))))

claude:
	@WORKBENCH_VOICE=$(VOICE) $(WORKBENCH) session start claude $(if $(filter 1,$(REMOTE)),--remote) $(RESUME_ARG)

claude-remote:
	@WORKBENCH_VOICE=$(VOICE) $(WORKBENCH) session start claude --remote $(RESUME_ARG)

claude-plain:
	@WORKBENCH_VOICE=0 $(WORKBENCH) session start claude $(RESUME_ARG)

# The session levels (docs/MODES.md). Unattended has nobody to talk to, so no
# voice; remote control stays, to look in from a phone.
claude-assisted:
	@WORKBENCH_VOICE=$(VOICE) $(WORKBENCH) session start claude --level assisted $(if $(filter 1,$(REMOTE)),--remote) $(RESUME_ARG)

claude-unattended:
	@WORKBENCH_VOICE=0 $(WORKBENCH) session start claude --level unattended $(if $(filter 1,$(REMOTE)),--remote) $(HOURS_ARG) $(RESUME_ARG)

codex:
	@$(WORKBENCH) session start codex

claude-shell:
	@$(WORKBENCH) shell claude

codex-shell:
	@$(WORKBENCH) shell codex

# The ssh-agent has to be running to take the key, so start the helpers
# first; they are left alone when they already run.
unlock:
	@$(WORKBENCH) helpers >/dev/null
	@$(WORKBENCH) unlock $(if $(HOURS),"$$HOURS")

workbench-up:
	@$(WORKBENCH) up

workbench-down:
	@$(WORKBENCH) down

workbench-status:
	@$(WORKBENCH) status

workbench-build:
	@$(WORKBENCH) build

workbench-pull:
	@$(WORKBENCH) pull

# Sessions (docs/SESSIONS.md): each `make claude` or `make codex` is one,
# with a random name. These act on one by name, and Tab lists the names.
# Targets named after a session, so Tab completes the name, only for the
# sessions in use: attach- for the running ones and those used in the last
# few days (WORKBENCH_RECENT_DAYS, default 3), shell- and stop- for the
# running ones. Any session at all goes by name instead: make session-attach
# S=<name>, and session-shell, session-stop and session-prune the same way
# (make sessions lists them).
WORKBENCH_RUNNING := $(shell $(WORKBENCH) session names --running 2>/dev/null)
WORKBENCH_RECENT := $(shell $(WORKBENCH) session names --recent 2>/dev/null)
define workbench_attach
.PHONY: attach-$(1)
attach-$(1):
	@WORKBENCH_VOICE=$$(VOICE) $$(WORKBENCH) session attach $(1) $$(if $$(filter 1,$$(REMOTE)),--remote)
endef
define workbench_running
.PHONY: shell-$(1) stop-$(1)
shell-$(1):
	@$$(WORKBENCH) session shell $(1)
stop-$(1):
	@$$(WORKBENCH) session stop $(1)
endef
$(foreach s,$(sort $(WORKBENCH_RUNNING) $(WORKBENCH_RECENT)),$(eval $(call workbench_attach,$(s))))
$(foreach s,$(WORKBENCH_RUNNING),$(eval $(call workbench_running,$(s))))

S ?=
export S
WORKBENCH_S = $(if $(S),"$$S",$(error which session? S=<name>; make sessions lists them))
.PHONY: session-attach session-shell session-stop session-prune
session-attach:
	@WORKBENCH_VOICE=$(VOICE) $(WORKBENCH) session attach $(WORKBENCH_S) $(if $(filter 1,$(REMOTE)),--remote)
session-shell:
	@$(WORKBENCH) session shell $(WORKBENCH_S)
session-stop:
	@$(WORKBENCH) session stop $(WORKBENCH_S)
session-prune:
	@$(WORKBENCH) session prune $(WORKBENCH_S)

sessions:
	@$(WORKBENCH) session list

prune:
	@$(WORKBENCH) session prune
