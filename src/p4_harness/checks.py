"""Run user-configured checks and bind evidence to the actual file snapshot."""
from pathlib import Path
import os
import signal
import subprocess
import time

from .common import HarnessError, digest, local_path, now, task_id


def verify(flow, name, agent=None):
    task_id(name)
    task = flow.active()
    flow.require_owner(task, agent)
    profile = flow.config.data.get("checks", {}).get(name)
    if not isinstance(profile, dict):
        raise HarnessError(f"Configure checks.{name} in .p4-harness/config.json first.")
    argv = profile.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
        raise HarnessError("A check requires a non-empty argv array; shell command strings are not accepted.")
    timeout = profile.get("timeout_seconds", 600)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise HarnessError("Check timeout_seconds must be a positive number.")
    cwd = local_path(flow.root, profile.get("cwd", "."))
    if not cwd.is_dir():
        raise HarnessError("Check cwd must be an existing directory within this workspace.")
    before = flow.collect(agent)
    folder = flow.state / "logs" / task["id"]
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / f"{name}-{time.time_ns()}.log"
    started = now()
    status, code, message = "failed", None, None
    with log.open("wb") as output:
        options = {"cwd": cwd, "stdout": output, "stderr": subprocess.STDOUT,
                   "stdin": subprocess.DEVNULL, "shell": False}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        try:
            proc = subprocess.Popen(argv, **options)
            try:
                code = proc.wait(timeout=timeout)
                status = "passed" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                status, message = "timeout", f"Exceeded {timeout} seconds"
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        except OSError as exc:
            message = str(exc)
    try:
        after = flow.snapshot(task)
        if after["snapshot_id"] != before["snapshot_id"]:
            status, message = "stale", "Source changed during verification; rerun against stable files."
    except HarnessError as exc:
        status, message = "stale", str(exc)
    record = {"status": status, "exit_code": code, "snapshot_id": before["snapshot_id"],
              "profile_id": digest(profile), "started_at": started, "finished_at": now(),
              "log": str(log), "message": message}
    task["checks"][name] = record
    flow.save(task)
    flow.event("check_finished", task=task["id"], check=name, status=status)
    # Bounded summary; full output stays in the log regardless of result.
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 4000))
        excerpt = stream.read().decode("utf-8", errors="replace")
    return {**record, "output_tail": excerpt}
