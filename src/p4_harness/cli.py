"""Human/agent CLI: JSON output, no model/API dependency."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .common import Config, HarnessError, operation_lock
from .workflow import Workflow


def parser():
    p = argparse.ArgumentParser(description="Shared Perforce workflow for Claude Code and Codex")
    p.add_argument("--workspace", help="P4 client root; auto-discovered from cwd when omitted")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("install", help="Install both agent adapters, preserving existing settings")
    init.add_argument("--client", required=True)
    init.add_argument("--port")
    init.add_argument("--user")
    init.add_argument("--p4-bin", default="p4")
    init.add_argument("--dry-run", action="store_true")
    for name in ("doctor", "status", "shelve", "finish"):
        sub.add_parser(name)
    for name in ("changes", "collect", "context"):
        view = sub.add_parser(name)
        view.add_argument("--full", action="store_true", help="Return full records instead of the compact view")
        view.add_argument("--limit", type=int, default=10, help="Files per summary page (1..200)")
        view.add_argument("--offset", type=int, default=0, help="Summary page offset")
        if name == "changes":
            view.add_argument("--since", help="Compare file entries against this task's full snapshot ID")
    begin = sub.add_parser("begin", help="Start one clean-scope task and create its pending CL")
    begin.add_argument("--task", required=True)
    begin.add_argument("--goal", required=True)
    begin.add_argument("--scope", action="append", required=True)
    begin.add_argument("--agent", choices=("claude", "codex"), required=True)
    prepare = sub.add_parser("prepare", help="Checkout existing files before an edit; reserve new file paths")
    prepare.add_argument("files", nargs="+")
    delete = sub.add_parser("delete")
    delete.add_argument("file")
    move = sub.add_parser("move")
    move.add_argument("source")
    move.add_argument("destination")
    check = sub.add_parser("verify")
    check.add_argument("profile", nargs="?")
    check.add_argument("--required", action="store_true", help="Run required checks in order, stopping on the first failure")
    check.add_argument("--full", action="store_true", help="Include complete check metadata and the legacy log tail")
    handoff = sub.add_parser("handoff")
    handoff.add_argument("--to", choices=("claude", "codex"), required=True)
    handoff.add_argument("--note", required=True)
    pause = sub.add_parser("pause")
    pause.add_argument("--note", required=True)
    resume = sub.add_parser("resume")
    resume.add_argument("--agent", choices=("claude", "codex"), required=True)
    hook = sub.add_parser("hook", help="Internal adapter: read one hook event from stdin")
    hook.add_argument("--agent", choices=("claude", "codex"), required=True)
    launch = sub.add_parser("launch", help="Start a CLI at the P4 root with its normal permissions")
    launch.add_argument("agent", choices=("claude", "codex"))
    launch.add_argument("args", nargs=argparse.REMAINDER)
    return p


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def apply_ignore(config):
    # P4IGNORE supports a semicolon-delimited list. Retain the effective existing setting.
    from .p4 import P4
    current = os.environ.get("P4IGNORE")
    if current is None:
        current = P4(config).ignore_setting()
        # Current P4 defaults; an explicitly empty environment value is preserved.
        if current is None:
            current = ".p4ignore;p4ignore.txt"
    own = str(config.root / ".p4-harness.p4ignore")
    values = [x for x in current.split(";") if x]
    if own not in values:
        values.append(own)
    os.environ["P4IGNORE"] = ";".join(values)


def main(argv=None):
    # Windows redirects and hook JSON must also use UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    event = None
    try:
        if args.command == "install":
            from .install import install
            emit(install(args.workspace or os.getcwd(), args.client, args.port, args.user, args.p4_bin, args.dry_run))
            return 0
        config = Config.load(args.workspace)
        if args.command == "hook":
            from .hooks import run_hook
            event = json.loads(sys.stdin.read())
            if not isinstance(event, dict):
                raise HarnessError("Hook input must be an object.")
            apply_ignore(config)
            with operation_lock(config.control):
                emit(run_hook(Workflow(config), args.agent, event))
            return 0
        apply_ignore(config)
        flow = Workflow(config)
        actor = os.environ.get("P4_HARNESS_AGENT")
        if args.command in {"changes", "collect", "context"}:
            from .reporting import page
            page([], args.limit, args.offset)  # Reject invalid pagination before collect can add files.
        if args.command == "verify" and bool(args.profile) == args.required:
            raise HarnessError("Specify exactly one profile or --required.")
        if args.command == "launch":
            flow.doctor()
            executable = shutil.which(args.agent)
            if not executable:
                raise HarnessError(f"{args.agent} is not installed or is not on PATH.")
            command = [executable]
            if args.agent == "codex":
                command += ["-c", 'project_root_markers=[".p4-harness"]', "-c", "features.hooks=true"]
            extra = args.args[1:] if args.args[:1] == ["--"] else args.args
            command += extra
            env = dict(os.environ, P4_HARNESS_AGENT=args.agent)
            return subprocess.call(command, cwd=config.root, env=env)
        with operation_lock(config.control):
            match args.command:
                case "doctor":
                    result = {**flow.doctor(), "active_task": flow.active(False),
                              "claude_cli": shutil.which("claude"), "codex_cli": shutil.which("codex")}
                    if result["active_task"]:
                        result["active_task"] = flow.summary(result["active_task"])
                case "status":
                    task = flow.active(False)
                    result = flow.summary(task) if task else {"active_task": None}
                case "begin":
                    result = flow.begin(args.task, args.goal, args.scope, args.agent)
                case "prepare":
                    result = flow.prepare(args.files, actor)
                case "changes":
                    from .reporting import snapshot_output
                    result = snapshot_output(flow, flow.snapshot(), since=args.since, full=args.full,
                                             limit=args.limit, offset=args.offset)
                case "collect":
                    from .reporting import snapshot_output
                    result = snapshot_output(flow, flow.collect(actor), full=args.full,
                                             limit=args.limit, offset=args.offset)
                case "context":
                    from .reporting import context_output
                    result = context_output(flow, full=args.full, limit=args.limit, offset=args.offset)
                case "delete":
                    result = flow.delete(args.file, actor)
                case "move":
                    result = flow.move(args.source, args.destination, actor)
                case "handoff":
                    result = flow.handoff(args.to, args.note, actor)
                case "pause":
                    result = flow.pause(args.note, actor)
                case "resume":
                    result = flow.resume(args.agent)
                case "shelve":
                    result = flow.shelve(actor)
                case "verify":
                    from .checks import verify, verify_required
                    from .reporting import check_output
                    if args.required:
                        result = verify_required(flow, actor)
                        result["checks"] = [check_output(row, full=args.full) for row in result["checks"]]
                    else:
                        result = check_output(verify(flow, args.profile, actor), full=args.full)
                    emit(result)
                    return 0 if result["status"] in {"passed", "not_configured"} else 1
                case "finish":
                    result = flow.finish(actor)
            emit(result)
            return 0
    except Exception as exc:
        if args.command == "hook":
            # PreToolUse must fail closed, including malformed inputs/configuration.
            if event is None or event.get("hook_event_name") == "PreToolUse":
                emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "p4-harness: " + str(exc)}})
                return 0
            emit({"systemMessage": "p4-harness: " + str(exc)})
            return 0
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
