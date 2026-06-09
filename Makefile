# Makefile for the goal-loop skill + Stop hook.
#
# `make install`   — copy skills/goal-loop/ into ~/.claude/skills/ AND register
#                    the Stop hook in settings.json (manual/dev path).
# `make uninstall` — remove the installed copy and the Stop-hook registration.
# `make test`      — run the deterministic test suite (no API key).
#
# Honors $CLAUDE_CONFIG_DIR (defaults to ~/.claude), like Claude Code itself.

CLAUDE_DIR := $(if $(CLAUDE_CONFIG_DIR),$(CLAUDE_CONFIG_DIR),$(HOME)/.claude)
SKILL_SRC  := skills/goal-loop
SKILL_DEST := $(CLAUDE_DIR)/skills/goal-loop

.PHONY: install uninstall test help

help:
	@echo "Targets:"
	@echo "  install     copy $(SKILL_SRC)/ to $(SKILL_DEST)/ + register Stop hook"
	@echo "  uninstall   delete $(SKILL_DEST)/ + unregister Stop hook"
	@echo "  test        run tests/run.sh (no API key needed)"

install:
	@mkdir -p "$(SKILL_DEST)"
	@rsync -a --delete "$(SKILL_SRC)/" "$(SKILL_DEST)/"
	@bash "$(SKILL_DEST)/scripts/install-hook.sh"
	@echo "Installed: $(SKILL_DEST) (+ Stop hook)"

uninstall:
	@-bash "$(SKILL_DEST)/scripts/install-hook.sh" --uninstall 2>/dev/null || true
	@rm -rf "$(SKILL_DEST)"
	@echo "Removed: $(SKILL_DEST) (+ Stop hook)"

test:
	@bash tests/run.sh
