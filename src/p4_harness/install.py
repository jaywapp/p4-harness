"""Install workspace-local adapters while preserving existing user configuration."""
from importlib.resources import files
from pathlib import Path
import base64
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import time

from .common import HarnessError, read_json


MARK_START = "<!-- p4-harness:start -->"
MARK_END = "<!-- p4-harness:end -->"


def managed_block(original, body):
    block = f"{MARK_START}\n{body.rstrip()}\n{MARK_END}"
    if MARK_START in original or MARK_END in original:
        if original.count(MARK_START) != 1 or original.count(MARK_END) != 1:
            raise HarnessError("Malformed p4-harness managed block; preserve and repair it before installing.")
        start, end = original.index(MARK_START), original.index(MARK_END)
        if end < start:
            raise HarnessError("Managed block markers are reversed.")
        return original[:start] + block + original[end + len(MARK_END):]
    return original.rstrip() + ("\n\n" if original.strip() else "") + block + "\n"


def runner_argv():
    checkout_runner = Path(__file__).resolve().parents[2] / "p4h.py"
    return [sys.executable, str(checkout_runner)] if checkout_runner.is_file() else [sys.executable, "-m", "p4_harness"]


def command_string(argv, windows=False):
    if not windows:
        return shlex.join(argv)
    # PowerShell literal arguments, encoded to avoid cmd/Bash interpolation of paths.
    script = "& " + " ".join("'" + value.replace("'", "''") + "'" for value in argv) + "; exit $LASTEXITCODE"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded


def merge_hooks(existing, argv, agent):
    result = json.loads(json.dumps(existing))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HarnessError("Existing hooks must be a JSON object.")
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "PreCompact", "Stop"):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise HarnessError(f"Existing {event} hooks must be a list.")
        # Only replace handlers carrying our marker, not the user's other hooks.
        kept = []
        for group in groups:
            group = dict(group)
            group["hooks"] = [h for h in group.get("hooks", []) if h.get("statusMessage") != "p4-harness"]
            if group["hooks"]:
                kept.append(group)
        handler = {"type": "command", "command": command_string(argv, os.name == "nt"),
                   "timeout": 60, "statusMessage": "p4-harness"}
        if agent == "codex":
            handler["commandWindows"] = command_string(argv, True)
        kept.append({"matcher": "*", "hooks": [handler]})
        hooks[event] = kept
    return result


def install(workspace, client, port=None, user=None, p4_bin="p4", dry_run=False):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise HarnessError("Workspace must be an existing directory (the actual P4 client root).")
    control = root / ".p4-harness"
    targets = {}

    def current(relative):
        path = root / relative
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
            raise HarnessError(f"Refusing installation through a symlink: {relative}")
        return path.read_text(encoding="utf-8-sig") if path.exists() else ""

    config_file = control / "config.json"
    current(".p4-harness/config.json")
    if config_file.exists():
        config = read_json(config_file)
        if config.get("workspace_root") != str(root) or config.get("client") != client:
            raise HarnessError("Existing installation belongs to another root/client. Preserve it; do not overwrite.")
        if (port is not None and port != config.get("port")) or (user is not None and user != config.get("user")):
            raise HarnessError("Existing port/user differs. Review config.json explicitly rather than overwrite it.")
    else:
        config = {"version": 1, "workspace_root": str(root), "client": client, "port": port, "user": user,
                  "p4_command": [p4_bin], "p4_timeout_seconds": 20, "max_scope_files": 20000,
                  "checks": {}, "required_checks": []}
    targets[".p4-harness/config.json"] = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    runner = runner_argv()
    base_argv = runner + ["--workspace", str(root)]
    command = ("& " + " ".join("'" + x.replace("'", "''") + "'" for x in base_argv)
               if os.name == "nt" else shlex.join(base_argv))
    templates = files("p4_harness").joinpath("templates")
    rules = templates.joinpath("rules.md").read_text(encoding="utf-8").replace("{{COMMAND}}", command)
    targets[".p4-harness/rules.md"] = rules
    targets["AGENTS.md"] = managed_block(current("AGENTS.md"),
        "Before working in this Perforce workspace, read `.p4-harness/rules.md` and follow its task/CL workflow.")
    targets["CLAUDE.md"] = managed_block(current("CLAUDE.md"), "@.p4-harness/rules.md")
    skill = templates.joinpath("p4-work", "SKILL.md").read_text(encoding="utf-8").replace("{{COMMAND}}", command)
    targets[".agents/skills/p4-work/SKILL.md"] = skill
    targets[".claude/skills/p4-work/SKILL.md"] = skill
    for agent, relative in (("claude", ".claude/settings.local.json"), ("codex", ".codex/hooks.json")):
        text = current(relative)
        try:
            existing = json.loads(text) if text else {}
        except ValueError as exc:
            raise HarnessError(f"Existing {relative} is not valid JSON; it has not been overwritten.") from exc
        if not isinstance(existing, dict):
            raise HarnessError(f"Existing {relative} must be a JSON object.")
        argv = runner + ["--workspace", str(root), "hook", "--agent", agent]
        targets[relative] = json.dumps(merge_hooks(existing, argv, agent), indent=2, ensure_ascii=False) + "\n"
    targets[".p4-harness.p4ignore"] = "\n".join([
        ".p4-harness/", ".agents/skills/p4-work/", ".claude/skills/p4-work/",
        ".claude/settings.local.json", ".codex/hooks.json", ".p4-harness.p4ignore",
        "AGENTS.md", "CLAUDE.md", ""])  # Existing tracked files remain tracked.
    changes = {relative: value for relative, value in targets.items() if current(relative) != value}
    for relative in changes:
        path = root / relative
        if path.exists() and not (path.stat().st_mode & stat.S_IWUSR):
            raise HarnessError(f"{relative} is read-only. Check it out in a configuration CL before installation.")
    if dry_run:
        return {"dry_run": True, "workspace": str(root), "files": list(changes)}
    backup = control / "backups" / str(time.time_ns())
    for relative, value in changes.items():
        path = root / relative
        if path.exists():
            copied = backup / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copied)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8", newline="\n")
    return {"workspace": str(root), "files": list(changes), "backup": str(backup) if backup.exists() else None,
            "next": "Run doctor. Start both clients with launch so P4IGNORE and project-root flags are applied."}
