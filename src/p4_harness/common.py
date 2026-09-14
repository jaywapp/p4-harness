"""Configuration, workspace paths, atomic state and cross-process locking."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
import time


class HarnessError(Exception):
    """An actionable refusal; source files must not be discarded to recover."""


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"Cannot read JSON file {path}: {exc}") from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def task_id(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise HarnessError("Task id must be 1-80 letters, digits, underscores or hyphens.")
    return value


PROTECTED = {".git", ".p4-harness", ".claude", ".codex", ".agents"}
PROTECTED_FILES = {"agents.md", "agents.override.md", "claude.md", "claude.local.md", ".p4ignore", ".p4-harness.p4ignore"}


def local_path(root, value, *, protected=True):
    """Resolve against the configured root, never the caller's changing cwd."""
    root = Path(root).resolve()
    raw = Path(value)
    if ".." in raw.parts or "..." in raw.parts or any(c in str(value) for c in "\n\r\x00*?"):
        raise HarnessError(f"Use a literal file/directory path without traversal or wildcards: {value}")
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
        lexical = Path(os.path.abspath(candidate)).relative_to(root)
    except ValueError as exc:
        raise HarnessError(f"Path is outside workspace: {value}") from exc
    # Reject even in-root symlink/junction aliases: one file has one ownership key.
    if os.path.normcase(str(root / lexical)) != os.path.normcase(str(resolved)):
        raise HarnessError(f"Use the physical path, not a symlink/junction: {value}")
    if protected and (any(p.lower() in PROTECTED for p in relative.parts)
                      or resolved.name.lower() in PROTECTED_FILES):
        raise HarnessError(f"Harness/agent configuration is outside source-edit scope: {value}")
    return resolved


def in_scope(root, path, scopes):
    path = Path(path)
    return any(path == root / s["path"] or
               (s["directory"] and (root / s["path"]) in path.parents) for s in scopes)


def p4_literal(path):
    """Escape Perforce revision/wildcard metacharacters, including literal percent."""
    return str(path).replace("%", "%25").replace("#", "%23").replace("@", "%40").replace("*", "%2A")


@dataclass
class Config:
    root: Path
    data: dict

    @property
    def control(self):
        return self.root / ".p4-harness"

    @classmethod
    def load(cls, location=None):
        start = Path(location or os.getcwd()).resolve()
        for root in (start, *start.parents):
            path = root / ".p4-harness" / "config.json"
            if path.is_file():
                if (root / ".p4-harness").is_symlink() or path.is_symlink():
                    raise HarnessError("Harness configuration must be a physical local file.")
                data = read_json(path)
                if data.get("version") != 1 or Path(data.get("workspace_root", "")).resolve() != root:
                    raise HarnessError("Workspace config root/version does not match this installation.")
                if not isinstance(data.get("client"), str) or not data["client"].strip():
                    raise HarnessError("Set a non-empty P4 client in config.json.")
                command = data.get("p4_command", ["p4"])
                if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
                    raise HarnessError("p4_command must be an argv array, e.g. [\"p4\"].")
                return cls(root, data)
        raise HarnessError("No .p4-harness/config.json found. Run install for the P4 client root first.")


@contextmanager
def operation_lock(control, wait_seconds=2):
    """OS releases this lock on process exit; never break a stale lock by deleting it."""
    state = Path(control) / "state"
    state.mkdir(parents=True, exist_ok=True)
    if state.is_symlink():
        raise HarnessError("State directory cannot be a symlink.")
    stream = (state / "operation.lock").open("a+b")
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise HarnessError("Another harness operation is running; retry after it finishes.")
                time.sleep(0.05)
        yield
    finally:
        # Closing also releases the lock, including on exceptions and Windows.
        stream.close()
