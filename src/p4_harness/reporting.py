"""Deterministic, bounded views of full local evidence; never decides check success."""
from collections import Counter
from pathlib import Path
import re

from .common import HarnessError, digest, local_path, read_json


def page(rows, limit=10, offset=0):
    if not 1 <= limit <= 200 or offset < 0:
        raise HarnessError("Use --limit 1..200 and a non-negative --offset.")
    selected = rows[offset:offset + limit]
    following = offset + len(selected)
    return selected, {"total": len(rows), "offset": offset, "shown": len(selected),
                      "truncated": offset > 0 or following < len(rows),
                      "next_offset": following if following < len(rows) else None}


def manifest_file(flow, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise HarnessError("Use the full 64-character snapshot_id returned by collect/changes.")
    return local_path(flow.root, str(flow.state / "manifests" / (identifier + ".json")), protected=False)


def load_manifest(flow, identifier):
    value = read_json(manifest_file(flow, identifier))
    if (not isinstance(value, dict) or value.get("snapshot_id") != identifier
            or digest({k: v for k, v in value.items() if k != "snapshot_id"}) != identifier):
        raise HarnessError("Snapshot contents do not match their id. Preserve the original evidence.")
    if not isinstance(value.get("files"), list):
        raise HarnessError("Snapshot has no valid file list.")
    return value


def snapshot_delta(before, after):
    # Never let a new task, sync baseline or client view silently inherit a prior comparison.
    for field in ("task", "change", "environment", "base_have_id"):
        if before.get(field) != after.get(field):
            raise HarnessError("--since must refer to this task, CL, environment and have baseline.")
    old = {row["path"]: row for row in before["files"]}
    new = {row["path"]: row for row in after["files"]}
    changes = []
    counts = {"introduced": 0, "updated": 0, "removed": 0, "unchanged": 0}
    for path in sorted(old.keys() | new.keys()):
        if old.get(path) == new.get(path):
            counts["unchanged"] += 1
            continue
        kind = "introduced" if path not in old else "removed" if path not in new else "updated"
        counts[kind] += 1
        changes.append({"path": path, "delta": kind, "before": old.get(path), "after": new.get(path)})
    return {"task": after["task"], "change": after["change"],
            "since": before["snapshot_id"], "snapshot_id": after["snapshot_id"],
            "counts": counts, "changes": changes}


def snapshot_output(flow, snapshot, *, since=None, full=False, limit=10, offset=0):
    if since:
        result = snapshot_delta(load_manifest(flow, since), snapshot)
        if not full:
            selected, paging = page(result["changes"], limit, offset)
            result["changes"] = [{"path": row["path"], "delta": row["delta"],
                                  "action": (row["after"] or row["before"])["action"]} for row in selected]
            result["page"] = paging
        result["manifest"] = str(manifest_file(flow, snapshot["snapshot_id"]))
        result["previous_manifest"] = str(manifest_file(flow, since))
        return result
    if full:
        return snapshot  # Preserve the original full-manifest schema.
    selected, paging = page(snapshot["files"], limit, offset)
    return {"task": snapshot["task"], "change": snapshot["change"],
            "snapshot_id": snapshot["snapshot_id"], "file_count": len(snapshot["files"]),
            "actions": dict(sorted(Counter(row["action"] for row in snapshot["files"]).items())),
            "files": [{"path": row["path"], "action": row["action"]} for row in selected],
            "page": paging, "manifest": str(manifest_file(flow, snapshot["snapshot_id"]))}


DIAGNOSTIC = re.compile(r"\b(?:errors?|fatal|fail(?:ed|ure|ures)?|exception|traceback|panic|assertionerror)\b", re.I)


def failure_excerpt(path, *, max_chars=3000, max_lines=24, scan_bytes=4 * 1024 * 1024):
    """Pick error context from a bounded prefix; fallback to the tail for unknown log formats.

    No verdict is inferred from this heuristic. A failed process remains failed even with no match.
    The original bytes remain on disk, including text omitted or clipped here.
    """
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as stream:
        prefix = stream.read(scan_bytes)
        lines = prefix.decode("utf-8", errors="replace").splitlines()
        selected, seen = set(), set()
        matches, clipped = 0, False
        for index, line in enumerate(lines):
            if not DIAGNOSTIC.search(line):
                continue
            matches += 1
            key = line.strip()
            if key in seen:
                clipped = True
                continue
            if len(selected) >= max_lines:
                clipped = True
                continue
            seen.add(key)
            for nearby in range(max(0, index - 2), min(len(lines), index + 4)):
                if len(selected) >= max_lines and nearby not in selected:
                    clipped = True
                    break
                selected.add(nearby)
        if selected:
            mode = "error_context"
            clipped = clipped or len(selected) < len(lines)
            rendered = []
            for i in sorted(selected):
                line = lines[i]
                if len(line) > 400:
                    match = DIAGNOSTIC.search(line)
                    start = max(0, match.start() - 120) if match else 0
                    line = ("..." if start else "") + line[start:start + 400] + "..."
                    clipped = True
                rendered.append(f"{i + 1}: {line}")
            text = "\n".join(rendered)
        else:
            mode = "tail_fallback"
            stream.seek(max(0, size - 8192))
            tail = stream.read().decode("utf-8", errors="replace").splitlines()
            text = "\n".join(tail[-max_lines:])
            clipped = size > 8192 or len(tail) > max_lines
    return {"selection": mode, "excerpt": text[:max_chars],
            "matched_lines_in_scan": matches, "scanned_bytes": len(prefix), "log_bytes": size,
            "truncated": clipped or size > scan_bytes or len(text) > max_chars}


def check_output(record, *, full=False):
    if full:
        return record
    result = {k: record[k] for k in ("check", "status", "exit_code", "snapshot_id", "log") if k in record}
    if record.get("message"):
        result["message"] = record["message"]
    if record["status"] != "passed":
        result["diagnostics"] = record["diagnostics"]
    return result


def context_output(flow, *, full=False, limit=10, offset=0):
    """A live read-only handoff view. Revalidate server state; do not cache authorization."""
    task = flow.active(False)
    references = {"rules": str(flow.config.control / "rules.md"),
                  "project_map": str(flow.config.control / "project-map.md"),
                  "workflows": str(flow.config.control / "workflows.md")}
    if not task:
        return {"active_task": None, "references": references, "next_action": "Inspect doctor, then begin a scoped task."}
    snapshot = flow.snapshot(task)
    profiles = flow.config.data.get("checks", {})
    required = flow.config.data.get("required_checks", [])
    checks = []
    for name in sorted(set(task["checks"]) | set(required)):
        record = task["checks"].get(name)
        if record is None:
            status = "missing"
        elif (record.get("snapshot_id") != snapshot["snapshot_id"] or name not in profiles
              or record.get("profile_id") != digest(profiles[name])):
            status = "stale"
        else:
            status = record["status"]
        checks.append({"check": name, "status": status, "required": name in required})
    pending = [row for row in checks if row["required"] and row["status"] != "passed"]
    report = {"task": task["id"], "change": task["change"], "owner": task["owner"], "status": task["status"],
              "goal": task["goal"], "scope": task["scope"], "handoff": task.get("handoff"),
              "pause_note": task.get("pause_note"),
              "verification": "not_configured" if not required else "pending" if pending else "passed",
              "checks": checks, "task_record": str(flow.state / "tasks" / (task["id"] + ".json")),
              "references": references}
    if not full:
        truncated = []
        for field in ("goal", "pause_note"):
            if isinstance(report[field], str) and len(report[field]) > 500:
                report[field] = report[field][:500]
                truncated.append(field)
        if report["handoff"]:
            report["handoff"] = dict(report["handoff"])
            if len(report["handoff"].get("note", "")) > 500:
                report["handoff"]["note"] = report["handoff"]["note"][:500]
                truncated.append("handoff.note")
        if len(report["scope"]) > 10:
            report["scope"] = report["scope"][:10]
            truncated.append("scope")
        report["truncated_fields"] = truncated
    report["changes"] = snapshot_output(flow, snapshot, full=full, limit=limit, offset=offset)
    return report
