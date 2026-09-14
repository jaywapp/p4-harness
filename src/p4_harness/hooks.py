"""Shared hook protocol for Claude Edit/Write and Codex apply_patch."""
import re
from pathlib import Path

from .common import HarnessError


def patch_paths(patch):
    if not isinstance(patch, str) or not patch.startswith("*** Begin Patch\n") or "*** End Patch" not in patch:
        raise HarnessError("Unrecognized apply_patch input; prepare files explicitly and use a standard patch.")
    paths = []
    for line in patch.splitlines():
        if line.startswith(("*** Delete File: ", "*** Move to: ")):
            raise HarnessError("Use p4h delete or p4h move for deletes/renames so Perforce records their actions.")
        if line.startswith(("*** Update File: ", "*** Add File: ")):
            paths.append(line.split(": ", 1)[1])
    if not paths:
        raise HarnessError("No file actions found in apply_patch.")
    return paths


def native_paths(tool, args):
    if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        path = args.get("file_path") or args.get("notebook_path")
        if not isinstance(path, str):
            raise HarnessError(f"Missing file path for {tool}.")
        return [path]
    if tool == "apply_patch":
        return patch_paths(args.get("command", args.get("patch")))
    # Known filesystem MCP shapes; this is not a universal MCP write firewall.
    if tool.startswith("mcp__") and re.search(r"(?:write|edit|patch)_file$", tool):
        path = args.get("path") or args.get("file_path")
        if not isinstance(path, str):
            raise HarnessError("Unknown filesystem MCP path schema; use prepare and a supported file editor.")
        return [path]
    return []


def shell_policy(command):
    """An accidental-command guard, deliberately not represented as a shell sandbox."""
    if not isinstance(command, str):
        return
    p4 = re.search(r"(?i)(?<![\w-])p4(?:\.exe)?(?:[\"']|\s)", command)
    if p4:
        rest = command[p4.end():]
        if re.search(r"(?i)\b(submit|revert|clean|sync|flush|update|edit|add|delete|move|reopen|shelve|unshelve|resolve|integrate|merge|copy|populate|reconcile|rec|obliterate|protect|typemap|unlock)\b", rest):
            raise HarnessError("Raw P4 mutation blocked. Use the task's p4h workflow; submit/sync/revert stay with the operator.")
        if re.search(r"(?i)\b(client|change|stream)\b", rest) and not re.search(r"(?:^|\s)-o(?:\s|$)", rest):
            raise HarnessError("P4 form mutation blocked; only read-only -o inspection is allowed here.")
        if re.search(r"(?i)\bprint\b", rest) and re.search(r"(?:^|\s)-o(?:\s|$)", rest):
            raise HarnessError("p4 print -o writes files; use a reviewed operator command outside the coding session.")
    if re.search(r"(?i)\b(?:attrib\s+-r|chmod\s+(?:\+w|u\+w|a\+w))\b", command):
        raise HarnessError("Do not clear read-only flags to bypass checkout. Use p4h prepare.")


def looks_read_only(command):
    if not isinstance(command, str) or any(x in command for x in (">", ";", "&&", "||", "`", "$(")):
        return False
    return bool(re.match(r"\s*(?:rg|pwd|ls|cat|head|tail|Get-Content|Get-ChildItem)(?:\s|$)", command, re.I))


def run_hook(flow, agent, event):
    name = event.get("hook_event_name")
    tool = str(event.get("tool_name", "")).split(".")[-1]
    args = event.get("tool_input") or {}
    if not isinstance(args, dict):
        raise HarnessError("Hook tool_input must be an object.")
    session = event.get("session_id")
    active = flow.active(False)
    if name == "SessionStart":
        text = "Perforce workspace. Read .p4-harness/rules.md. Use p4h doctor then begin before editing."
        if active:
            text += f" Active task={active['id']}; CL={active['change']}; owner={active['owner']}; status={active['status']}."
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}
    if name == "PreToolUse":
        paths = native_paths(tool, args)
        if paths:
            cwd = Path(event.get("cwd") or flow.root)
            if not cwd.is_absolute():
                raise HarnessError("Hook cwd must be absolute.")
            paths = [str(Path(p) if Path(p).is_absolute() else cwd / p) for p in paths]
            flow.prepare(paths, agent, session)
        elif tool in {"Bash", "PowerShell", "exec_command", "shell_command"}:
            command = args.get("command", args.get("cmd", ""))
            shell_policy(command)
            read_harness = bool(re.search(r"(?:p4h(?:\.py)?|p4_harness)\b.*\b(?:doctor|status|changes)\b", command))
            recovery = bool(re.search(r"(?:p4h(?:\.py)?|p4_harness)\b.*\bresume\b", command))
            if active and not looks_read_only(command) and not read_harness:
                if not (recovery and active["status"] == "paused"):
                    flow.require_owner(active, agent, session)
        # No allow decision: preserve the user's ordinary permission checks.
        return {}
    if name == "PostToolUse" and active:
        if native_paths(tool, args):
            active["checks"] = {}
            flow.save(active)
            flow.event("native_edit", task=active["id"], agent=agent, tool=tool)
        return {}
    if name in {"Stop", "PreCompact", "SessionEnd"} and active:
        flow.event(name, task=active["id"], agent=agent)
        # Advisory, not an endless Stop loop or an implicit shelf/submit operation.
        if name != "SessionEnd":
            return {"systemMessage": f"P4 task {active['id']} / CL {active['change']} remains {active['status']}. Use finish for checked handoff or pause to preserve work."}
    return {}
