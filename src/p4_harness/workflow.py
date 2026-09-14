"""Task ownership, explicit checkout, manifests, shelving and handoff."""
from pathlib import Path
import json
import os
import re

from .common import (HarnessError, digest, file_digest, in_scope, local_path,
                     now, p4_literal, read_json, task_id, write_json)
from .p4 import P4


class Workflow:
    # Public methods run under operation_lock in the CLI/hook adapters.
    def __init__(self, config, p4=None):
        self.config = config
        self.root = config.root
        self.p4 = p4 or P4(config)
        self.state = config.control / "state"

    def active(self, required=True):
        pointer = self.state / "active.json"
        if not pointer.exists():
            if required:
                raise HarnessError("No active task. Run begin --task ... --agent claude|codex --scope ... first.")
            return None
        value = read_json(pointer)
        return read_json(self.state / "tasks" / (task_id(value["id"]) + ".json"))

    def save(self, task):
        task["updated_at"] = now()
        write_json(self.state / "tasks" / (task_id(task["id"]) + ".json"), task)

    def event(self, name, **data):
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": now(), "event": name, **data}, ensure_ascii=False) + "\n")

    def doctor(self):
        infos = self.p4.stats("info")
        if len(infos) != 1:
            raise HarnessError("P4 info did not return an unambiguous client.")
        info = infos[0]
        if info.get("clientName") != self.config.data["client"]:
            raise HarnessError("Effective P4CLIENT does not match the installed workspace.")
        if not info.get("clientRoot") or Path(info["clientRoot"]).resolve() != self.root:
            raise HarnessError("Install at the actual client root reported by p4 info; root does not match.")
        if self.config.data.get("user") and info.get("userName") != self.config.data["user"]:
            raise HarnessError("Effective P4USER does not match the installation.")
        specs = self.p4.stats("client", "-o", self.config.data["client"])
        if len(specs) != 1:
            raise HarnessError("Cannot inspect client specification.")
        spec = specs[0]
        mapping = {k: v for k, v in spec.items() if k.startswith(("View", "AltRoots")) or
                   k in {"Root", "Stream", "StreamAtChange", "LineEnd", "Options", "Host"}}
        return {"client": info["clientName"], "root": str(self.root),
                "user": info.get("userName"), "server": info.get("serverAddress"),
                "stream": spec.get("Stream"), "mapping_id": digest(mapping)}

    def require_owner(self, task, agent=None, session=None):
        if task["status"] != "active":
            raise HarnessError(f"Task is {task['status']}; use resume before editing.")
        if agent and task["owner"] != agent:
            raise HarnessError(f"Task belongs to {task['owner']}; use an explicit handoff before {agent} edits.")
        if session and task.get("session_id") not in (None, session):
            raise HarnessError("Another session owns this workspace task. Handoff/resume explicitly.")
        if session and not task.get("session_id"):
            task["session_id"] = session
            self.save(task)

    def check_environment(self, task):
        actual = self.doctor()
        if actual != task["environment"]:
            raise HarnessError("P4 client/user/server/view changed during the task. Restore the recorded environment.")
        rows = self.p4.stats("change", "-o", task["change"])
        if (len(rows) != 1 or rows[0].get("Status") != "pending"
                or rows[0].get("Client") != actual["client"] or rows[0].get("User") != actual["user"]):
            raise HarnessError("Task CL is no longer pending or belongs to another client/user. Inspect it before continuing.")

    def scope_specs(self, scopes):
        return [p4_literal(self.root / s["path"]) + ("/..." if s["directory"] else "") for s in scopes]

    def have(self, scopes):
        records = self.p4.stats("have", *self.scope_specs(scopes), empty_ok=True)
        if len(records) > self.config.data.get("max_scope_files", 20000):
            raise HarnessError("Task scope contains too many tracked files. Choose a smaller directory.")
        return sorted([{"depot": r["depotFile"], "rev": str(r["haveRev"])} for r in records],
                      key=lambda x: x["depot"])

    def preview(self, scopes):
        rows = self.p4.stats("reconcile", "-n", "-f", *self.scope_specs(scopes), empty_ok=True)
        # A P4CONFIG P4IGNORE value takes precedence over the launch environment.
        # Exclude only untracked installation artifacts; never hide tracked edits/deletes.
        installed = {"AGENTS.md", "CLAUDE.md", ".p4-harness.p4ignore", ".codex/hooks.json",
                     ".claude/settings.local.json", ".agents/skills/p4-work/SKILL.md",
                     ".claude/skills/p4-work/SKILL.md"}
        result = []
        for row in rows:
            try:
                rel = Path(row["clientFile"]).resolve().relative_to(self.root).as_posix()
            except (KeyError, ValueError):
                result.append(row)
                continue
            if row.get("action") == "add" and (rel in installed or rel.startswith(".p4-harness/")):
                continue
            result.append(row)
        return result

    def begin(self, identifier, goal, scopes, agent):
        task_id(identifier)
        if self.active(False):
            raise HarnessError("An active task already exists; finish, pause/resume, or handoff it.")
        if (self.state / "tasks" / (identifier + ".json")).exists():
            raise HarnessError("Task id already exists. Use a new id; task history is retained.")
        if not scopes or not goal.strip():
            raise HarnessError("A goal and at least one explicit scope are required.")
        normalized = []
        for value in scopes:
            path = local_path(self.root, value)
            normalized.append({"path": path.relative_to(self.root).as_posix(), "directory": path.is_dir()})
        environment = self.doctor()
        if self.p4.stats("opened", *self.scope_specs(normalized), empty_ok=True) or self.preview(normalized):
            raise HarnessError("Scope has existing opened/offline changes. Preserve them and choose a clean, narrower scope.")
        baseline = self.have(normalized)
        task = {"id": identifier, "goal": goal, "scope": normalized, "owner": agent,
                "session_id": None, "status": "active", "environment": environment,
                "base_have": baseline, "prepared": {}, "checks": {}, "shelf": None,
                "created_at": now()}
        result = self.p4.run("change", "-i", form={"Change": "new", "Client": environment["client"],
                             "Description": f"[p4-harness:{identifier}] {goal}\nOwner: {agent}\n"})
        text = "\n".join(str(r.get("data", "")) for r in result)
        match = re.search(r"Change (\d+) created", text)
        if not match:
            raise HarnessError("P4 did not return the created CL number. Inspect pending changes before retrying.")
        task["change"] = int(match.group(1))
        self.save(task)
        write_json(self.state / "active.json", {"id": identifier})
        self.event("task_started", task=identifier, change=task["change"], owner=agent)
        return self.summary(task)

    def summary(self, task):
        return {k: task.get(k) for k in ("id", "goal", "owner", "status", "change", "scope", "shelf", "handoff", "pause_note")}

    def mapped(self, path):
        rows = self.p4.stats("where", p4_literal(path), empty_ok=True)
        rows = [r for r in rows if "unmap" not in r and r.get("path")]
        if len(rows) != 1 or Path(rows[0]["path"]).resolve() != path:
            raise HarnessError(f"Path does not have one writable P4 mapping: {path}")
        return rows[0]

    def fstat(self, path):
        rows = self.p4.stats("fstat", "-T", "depotFile,clientFile,headType,headAction,haveRev,action,change,unresolved",
                            p4_literal(path), empty_ok=True)
        if len(rows) > 1:
            raise HarnessError(f"Ambiguous file mapping: {path}")
        return rows[0] if rows else {}

    def checked_path(self, task, value):
        path = local_path(self.root, value)
        if not in_scope(self.root, path, task["scope"]):
            raise HarnessError(f"Path is outside task scope: {value}")
        if path.is_dir():
            raise HarnessError("Prepare individual files, not a directory.")
        self.mapped(path)
        return path

    def prepare(self, values, agent=None, session=None):
        task = self.active()
        self.require_owner(task, agent, session)
        self.check_environment(task)
        planned = []
        for value in values:
            path = self.checked_path(task, value)
            rel = path.relative_to(self.root).as_posix()
            meta = self.fstat(path)
            if meta.get("action") and str(meta.get("change")) != str(task["change"]):
                raise HarnessError(f"File belongs to another CL: {rel}")
            if meta.get("unresolved"):
                raise HarnessError(f"Resolve is required before editing: {rel}")
            tracked = bool(meta.get("headType") and meta.get("headAction") not in ("delete", "move/delete"))
            if tracked and not meta.get("haveRev"):
                raise HarnessError(f"File has not been synced: {rel}")
            if rel not in task["prepared"]:
                if tracked and (not path.is_file() or self.p4.stats("reconcile", "-n", "-f", p4_literal(path), empty_ok=True)):
                    raise HarnessError(f"Unowned offline change at {rel}; preserve it before continuing.")
                if not tracked and path.exists():
                    raise HarnessError(f"Existing untracked file is not owned by this task: {rel}")
            if meta.get("action") in ("delete", "move/delete"):
                raise HarnessError(f"File is marked for deletion: {rel}")
            planned.append((path, rel, meta, tracked))
        # Validate the whole request before the first checkout. Each success is durable.
        for path, rel, meta, tracked in planned:
            if tracked and not meta.get("action"):
                self.p4.run("edit", "-c", task["change"], p4_literal(path))
                opened = self.fstat(path)
                if opened.get("action") != "edit" or str(opened.get("change")) != str(task["change"]):
                    raise HarnessError(f"Checkout did not open the file in this task CL: {rel}")
            task["prepared"].setdefault(rel, {"new": not tracked, "prepared_at": now()})
            self.save(task)
        self.event("files_prepared", task=task["id"], files=[x[1] for x in planned])
        return {"change": task["change"], "prepared": [x[1] for x in planned]}

    def collect(self, agent=None):
        task = self.active()
        self.require_owner(task, agent)
        self.check_environment(task)
        for rel, item in task["prepared"].items():
            path = self.checked_path(task, rel)
            if item["new"] and path.is_file() and not self.fstat(path).get("action"):
                # `add -f` encodes literal filename characters, unlike revision filespec commands.
                # It does not bypass P4IGNORE (that would require -I, which is never used here).
                if any(c in str(path) for c in "@#%"):
                    self.p4.run("add", "-f", "-c", task["change"], str(path))
                else:
                    self.p4.run("add", "-c", task["change"], str(path))
                if self.fstat(path).get("action") != "add":
                    raise HarnessError(f"File was not added (check P4IGNORE): {rel}")
        return self.snapshot(task)

    def snapshot(self, task=None):
        task = task or self.active()
        self.check_environment(task)
        if self.have(task["scope"]) != task["base_have"]:
            raise HarnessError("Task baseline revisions changed. Do not validate against a silently changed sync state.")
        if self.preview(task["scope"]):
            raise HarnessError("Unregistered/offline changes remain in task scope. Prepare/add/delete explicitly, then collect.")
        scoped = self.p4.stats("opened", *self.scope_specs(task["scope"]), empty_ok=True)
        if any(str(r.get("change")) != str(task["change"]) for r in scoped):
            raise HarnessError("Another CL now has files open in this task's scope.")
        rows = self.p4.stats("opened", "-c", task["change"], empty_ok=True)
        files = []
        for row in rows:
            locations = self.p4.stats("where", row["depotFile"])
            locations = [x for x in locations if x.get("path") and "unmap" not in x]
            if len(locations) != 1:
                raise HarnessError("Cannot resolve changed file to one physical path.")
            path = self.checked_path(task, locations[0]["path"])
            rel = path.relative_to(self.root).as_posix()
            if rel not in task["prepared"]:
                raise HarnessError(f"CL contains a file not prepared by this task: {rel}")
            deleted = row["action"] in ("delete", "move/delete")
            if not deleted and not path.is_file():
                raise HarnessError(f"Opened file is missing: {rel}")
            meta = self.fstat(path)
            if meta.get("unresolved"):
                raise HarnessError(f"Unresolved file: {rel}")
            files.append({"path": rel, "depot": row["depotFile"], "action": row["action"],
                          "type": row.get("type"), "have_rev": meta.get("haveRev"),
                          "sha256": None if deleted else file_digest(path)})
        files.sort(key=lambda x: x["path"])
        value = {"task": task["id"], "change": task["change"], "environment": task["environment"],
                 "base_have_id": digest(task["base_have"]), "files": files}
        value["snapshot_id"] = digest(value)
        destination = self.state / "manifests" / (value["snapshot_id"] + ".json")
        write_json(destination, value)
        return value

    def delete(self, value, agent=None):
        task = self.active()
        self.require_owner(task, agent)
        self.check_environment(task)
        path = self.checked_path(task, value)
        rel = path.relative_to(self.root).as_posix()
        meta = self.fstat(path)
        if meta.get("action"):
            raise HarnessError("Delete requires a clean, unopened tracked file; existing edits are preserved.")
        if not meta.get("haveRev") or not path.is_file() or self.p4.stats("reconcile", "-n", "-f", p4_literal(path), empty_ok=True):
            raise HarnessError("Delete requires a clean, synced tracked file.")
        self.p4.run("delete", "-c", task["change"], p4_literal(path))
        task["prepared"][rel] = {"new": False, "prepared_at": now()}
        self.save(task)
        return {"deleted": rel, "change": task["change"]}

    def move(self, source, destination, agent=None):
        task = self.active()
        self.require_owner(task, agent)
        self.check_environment(task)
        src = self.checked_path(task, source)
        dst = self.checked_path(task, destination)
        if dst.exists() or self.fstat(dst).get("headType"):
            raise HarnessError("Move destination must be new and absent.")
        self.prepare([str(src)], agent)
        task = self.active()
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.p4.run("move", "-c", task["change"], p4_literal(src), p4_literal(dst))
        task["prepared"][dst.relative_to(self.root).as_posix()] = {"new": True, "prepared_at": now()}
        self.save(task)
        return {"source": source, "destination": destination, "change": task["change"]}

    def handoff(self, agent, note, caller=None):
        task = self.active()
        self.require_owner(task, caller)
        snapshot = self.collect(caller)
        old_owner = task["owner"]
        task.update(owner=agent, session_id=None, handoff={"from": old_owner, "note": note,
                                                        "snapshot_id": snapshot["snapshot_id"], "at": now()})
        self.save(task)
        self.event("handoff", task=task["id"], previous=old_owner, owner=agent)
        return self.summary(task)

    def pause(self, note, agent=None):
        task = self.active()
        self.require_owner(task, agent)
        task.update(status="paused", pause_note=note, session_id=None)
        self.save(task)
        return self.summary(task)

    def resume(self, agent):
        task = self.active()
        self.check_environment(task)
        task.update(status="active", owner=agent, session_id=None)
        self.save(task)
        self.event("resumed", task=task["id"], owner=agent)
        return self.summary(task)

    def shelve(self, agent=None):
        task = self.active()
        self.require_owner(task, agent)
        snapshot = self.collect(agent)
        if not snapshot["files"]:
            raise HarnessError("There are no files to shelve.")
        # Replace only this task's own shelf. No submit, force-unshelve or cleanup.
        args = ["shelve", "-c", task["change"]]
        if task.get("shelf"):
            args.append("-r")
        self.p4.run(*args)
        if self.snapshot(task)["snapshot_id"] != snapshot["snapshot_id"]:
            raise HarnessError("Files changed while shelving. Shelf is not a verified snapshot; inspect and retry.")
        task["shelf"] = {"change": task["change"], "snapshot_id": snapshot["snapshot_id"], "at": now()}
        self.save(task)
        return task["shelf"]

    def finish(self, agent=None):
        task = self.active()
        self.require_owner(task, agent)
        snapshot = self.collect(agent)
        required = self.config.data.get("required_checks", [])
        profiles = self.config.data.get("checks", {})
        for name in required:
            check = task["checks"].get(name, {})
            if (check.get("status") != "passed" or check.get("snapshot_id") != snapshot["snapshot_id"]
                    or name not in profiles or check.get("profile_id") != digest(profiles[name])):
                raise HarnessError(f"Required check {name!r} is missing, failed, or stale. Run verify {name}.")
        report = {**self.summary(task), "snapshot": snapshot, "checks": task["checks"],
                  "verification": "passed" if required else "not_configured", "submitted": False,
                  "next_action": "Review the CL and submit through the existing human/team process."}
        destination = self.state / "reports" / (task["id"] + ".json")
        write_json(destination, report)
        task.update(status="prepared", final_snapshot=snapshot["snapshot_id"], report=str(destination))
        self.save(task)
        (self.state / "active.json").unlink()
        self.event("task_prepared", task=task["id"], verification=report["verification"])
        return {"task": task["id"], "change": task["change"], "snapshot_id": snapshot["snapshot_id"],
                "verification": report["verification"], "report": str(destination), "submitted": False}
