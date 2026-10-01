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
.PHONY: claude codex claude-shell codex-shell claude-remote claude-plain unlock workbench-help workbench-up workbench-down workbench-status workbench-build workbench-pull

WORKBENCH := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))/workbench
VOICE ?= 1
REMOTE ?= 1

# A repository's own `help` target lists these by depending on this one.
workbench-help:
	@printf '%s\n' \
		'Workbench:' \
		'  claude                      Claude Code in its workbench, here, with voice (hold space) and remote control (REMOTE=0: without).' \
		'  claude-remote               The same as claude, whatever REMOTE says.' \
		'  claude-plain                Claude Code with neither voice nor remote control.' \
		'  codex                       Run Codex in its workbench, here (starts it if needed).' \
		'  claude-shell                A terminal in the Claude workbench.' \
		'  codex-shell                 A terminal in the Codex workbench.' \
		'  unlock                      Unlock the ssh key for git push, for 8 hours.' \
		'  workbench-up                Start this repository workbenches and L2 engine, and the shared services.' \
		'  workbench-down              Stop them; the shared services too when nothing else uses them.' \
		'  workbench-status            What is running.' \
		'  workbench-build             Build every image locally.' \
		'  workbench-pull              Or pull the published images instead.'
	@$(foreach a,$(WORKBENCH_ACCOUNTS),printf '  %-27s %s\n' 'claude-$(a), codex-$(a)' 'The same, logged in as $(a) (-shell, and claude-$(a)-remote, -plain).';)

# One more set of targets per account in WORKBENCH_ACCOUNTS (claude-personal,
# codex-personal and their shells), so Tab lists them too.
WORKBENCH_ACCOUNTS := $(shell $(WORKBENCH) accounts 2>/dev/null)
define workbench_account
.PHONY: claude-$(1) codex-$(1) claude-$(1)-shell codex-$(1)-shell claude-$(1)-remote claude-$(1)-plain
claude-$(1):
	@WORKBENCH_VOICE=$$(VOICE) $$(WORKBENCH) $$(if $$(filter 1,$$(REMOTE)),remote )claude-$(1)
codex-$(1):
	@$$(WORKBENCH) codex-$(1)
claude-$(1)-shell:
	@$$(WORKBENCH) shell claude-$(1)
codex-$(1)-shell:
	@$$(WORKBENCH) shell codex-$(1)
claude-$(1)-remote:
	@WORKBENCH_VOICE=$$(VOICE) $$(WORKBENCH) remote claude-$(1)
claude-$(1)-plain:
	@WORKBENCH_VOICE=0 $$(WORKBENCH) claude-$(1)
endef
$(foreach a,$(WORKBENCH_ACCOUNTS),$(eval $(call workbench_account,$(a))))

claude:
	@WORKBENCH_VOICE=$(VOICE) $(WORKBENCH) $(if $(filter 1,$(REMOTE)),remote )claude

claude-remote:
	@WORKBENCH_VOICE=$(VOICE) $(WORKBENCH) remote claude

claude-plain:
	@WORKBENCH_VOICE=0 $(WORKBENCH) claude

codex:
	@$(WORKBENCH) codex

claude-shell:
	@$(WORKBENCH) shell claude

codex-shell:
	@$(WORKBENCH) shell codex

# The ssh-agent has to be running to take the key, so start the helpers
# first; they are left alone when they already run.
unlock:
	@$(WORKBENCH) helpers >/dev/null
	@$(WORKBENCH) unlock

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
