"""Perforce argv execution and the documented -G/-ztag marshal protocol."""
import io
import marshal
import subprocess

from .common import HarnessError


def decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    if isinstance(value, dict):
        return {decode(k): decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(x) for x in value]
    return value


class P4:
    def __init__(self, config):
        self.config = config

    def ignore_setting(self):
        # Unlike server commands, `p4 set` emits plain text even with -G.
        result = subprocess.run([*self.config.data.get("p4_command", ["p4"]), "-d", str(self.config.root), "set", "P4IGNORE"],
                                cwd=self.config.root, capture_output=True,
                                timeout=self.config.data.get("p4_timeout_seconds", 20), shell=False)
        if result.returncode:
            raise HarnessError("Could not inspect effective P4IGNORE with p4 set.")
        for line in result.stdout.decode("utf-8", errors="strict").splitlines():
            if line.startswith("P4IGNORE="):
                return line.split("=", 1)[1].split(" (", 1)[0].strip()
        return None

    def run(self, *args, form=None, empty_ok=False):
        cfg = self.config.data
        command = list(cfg.get("p4_command", ["p4"])) + ["-d", str(self.config.root), "-ztag", "-G"]
        for name, flag in (("port", "-p"), ("user", "-u"), ("client", "-c")):
            if cfg.get(name):
                command += [flag, cfg[name]]
        command += [str(x) for x in args]
        payload = None
        if form is not None:
            payload = marshal.dumps({k.encode(): str(v).encode("utf-8") for k, v in form.items()}, 0)
        try:
            result = subprocess.run(command, input=payload, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=self.config.root,
                                    timeout=cfg.get("p4_timeout_seconds", 20), shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HarnessError(f"P4 {args[0]} could not run: {exc}") from exc
        if len(result.stdout) > cfg.get("max_p4_output_bytes", 32 * 1024 * 1024):
            raise HarnessError("P4 result is too large; narrow the task scope.")
        stream = io.BytesIO(result.stdout)
        records = []
        try:
            while stream.tell() < len(result.stdout):
                item = marshal.load(stream)
                if not isinstance(item, dict):
                    raise ValueError("expected dictionary")
                records.append(decode(item))
        except (EOFError, ValueError, TypeError) as exc:
            raise HarnessError("Invalid P4 -G output. Check p4_command and client encoding.") from exc
        errors = [r for r in records if r.get("code") == "error"]
        serious = [r for r in errors if not (empty_ok and str(r.get("generic")) == "17")]
        if serious or (result.returncode and not (empty_ok and errors and not serious)):
            detail = "; ".join(str(r.get("data", r)) for r in serious)
            detail = detail or result.stderr.decode("utf-8", errors="replace") or "unknown P4 failure"
            raise HarnessError(f"P4 {args[0]} failed: {detail[:2500].strip()}")
        return [r for r in records if r.get("code") != "error"]

    def stats(self, *args, **kwargs):
        return [r for r in self.run(*args, **kwargs) if r.get("code") == "stat"]
