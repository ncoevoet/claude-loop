#!/usr/bin/env bash
# install-hook.sh — register (or remove) the goal-loop Stop hook in the user's
# Claude Code settings.json. Used by `make install` for the manual/dev path;
# the plugin-install path uses hooks/hooks.json instead and does NOT need this.
#
# Idempotent: re-running never duplicates the entry (dedupe by command path).
# Usage: install-hook.sh [--uninstall]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK_CMD="$HERE/goal-loop-gate.sh"
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"

mode=install
[ "${1:-}" = "--uninstall" ] && mode=uninstall

command -v python3 >/dev/null 2>&1 || { echo "install-hook: python3 required" >&2; exit 1; }

SETTINGS="$SETTINGS" HOOK_CMD="$HOOK_CMD" MODE="$mode" python3 <<'PY'
import json, os, sys

settings_path = os.environ["SETTINGS"]
hook_cmd = os.environ["HOOK_CMD"]
mode = os.environ["MODE"]

os.makedirs(os.path.dirname(settings_path), exist_ok=True)
try:
    with open(settings_path) as fh:
        cfg = json.load(fh)
    if not isinstance(cfg, dict):
        cfg = {}
except FileNotFoundError:
    cfg = {}
except Exception as ex:
    print(f"install-hook: {settings_path} is not valid JSON ({ex}); refusing to overwrite", file=sys.stderr)
    sys.exit(1)

hooks = cfg.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])
if not isinstance(stop, list):
    print("install-hook: hooks.Stop is not a list; refusing to touch it", file=sys.stderr)
    sys.exit(1)


def mentions_cmd(group):
    return any(
        isinstance(h, dict) and h.get("command") == hook_cmd
        for h in (group.get("hooks", []) if isinstance(group, dict) else []))


# Remove any existing goal-loop entry first (idempotent for both modes).
stop = [g for g in stop if not mentions_cmd(g)]

if mode == "install":
    stop.append({"hooks": [{"type": "command", "command": hook_cmd, "timeout": 30}]})

hooks["Stop"] = stop
tmp = settings_path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
os.replace(tmp, settings_path)
print(f"install-hook: {mode}ed Stop hook -> {settings_path}")
PY
