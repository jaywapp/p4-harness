import base64
import io
import json
import marshal
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from p4_harness.common import Config, HarnessError, local_path, p4_literal
from p4_harness.hooks import patch_paths, shell_policy
from p4_harness.install import command_string, install
from p4_harness.p4 import P4


class UnitTests(unittest.TestCase):
    def test_p4_preserves_errors_and_accepts_only_explicit_empty_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(Path(tmp), {"client": "ws"})
            records = marshal.dumps({b"code": b"error", b"generic": 6, b"data": b"ticket expired"})
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, records, b"")):
                with self.assertRaisesRegex(HarnessError, "ticket expired"):
                    P4(cfg).stats("opened", empty_ok=True)
            records = marshal.dumps({b"code": b"error", b"generic": 17, b"data": b"no files"})
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, records, b"")):
                self.assertEqual(P4(cfg).stats("opened", empty_ok=True), [])
                with self.assertRaises(HarnessError):
                    P4(cfg).run("edit", "file")

    def test_p4_uses_argv_and_marshals_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(Path(tmp), {"client": "ws", "port": "ssl:p4:1666", "user": "dev"})
            output = marshal.dumps({b"code": b"info", b"data": b"Change 123 created."})
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, b"")) as run:
                P4(cfg).run("change", "-i", form={"Description": "first\nsecond"})
                args, options = run.call_args
                self.assertIn("-G", args[0])
                self.assertEqual(options["shell"], False)
                self.assertEqual(marshal.loads(options["input"])[b"Description"], b"first\nsecond")

    def test_malformed_protocol_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"not a p4 record", b"")):
                with self.assertRaises(HarnessError):
                    P4(Config(Path(tmp), {"client": "ws"})).run("info")

    def test_physical_paths_and_metadata_are_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Windows TEMP can use an 8.3 alias (RUNNER~1); compare physical roots.
            root = Path(tmp).resolve()
            for value in ("../outside", ".p4-harness/state/file", ".codex/hooks.json", "AGENTS.md", "src/..."):
                with self.assertRaises(HarnessError):
                    local_path(root, value)
            self.assertEqual(local_path(root, "src/file with space.txt"), root / "src/file with space.txt")
            target = root / "real"
            target.mkdir()
            try:
                (root / "alias").symlink_to(target, target_is_directory=True)
            except OSError:
                return  # Windows runner may not allow symlinks.
            with self.assertRaises(HarnessError):
                local_path(root, "alias/a.txt")

    def test_literal_p4_metacharacters(self):
        self.assertEqual(p4_literal("a@b#c%d"), "a%40b%23c%25d")

    def test_codex_patch_collects_all_files_before_mutation(self):
        content = "*** Begin Patch\n*** Update File: src/a.txt\n@@\n-a\n+b\n*** Add File: src/b.txt\n+x\n*** End Patch"
        self.assertEqual(patch_paths(content), ["src/a.txt", "src/b.txt"])
        with self.assertRaisesRegex(HarnessError, "p4h delete"):
            patch_paths(content.replace("*** Add File:", "*** Delete File:"))
        with self.assertRaises(HarnessError):
            patch_paths("unrecognized")

    def test_direct_p4_mutation_guard_with_global_options_and_exe_path(self):
        for command in ('p4 -c ws submit -c 123', '"C:\\Program Files\\Perforce\\p4.exe" -p server revert //...',
                        'p4 client -i', 'p4 reconcile -w //...', 'p4 print -o src/a //depot/a'):
            with self.assertRaises(HarnessError):
                shell_policy(command)
        shell_policy("p4 -c ws opened")
        shell_policy("p4 client -o ws")

    def test_install_preserves_user_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="p4 workspace ") as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            prior = {"permissions": {"deny": ["Bash(rm *)"]}, "hooks": {"PreToolUse": [{
                "matcher": "Bash", "hooks": [{"type": "command", "command": "custom-policy"}]}]}}
            (root / ".claude/settings.local.json").write_text(json.dumps(prior))
            (root / "AGENTS.md").write_text("# Existing project rules\nKeep this content.\n")
            preview = install(root, "test-ws", dry_run=True)
            self.assertTrue(preview["dry_run"])
            self.assertFalse((root / ".p4-harness").exists())
            first = install(root, "test-ws")
            self.assertTrue(first["files"])
            self.assertIn("Keep this content.", (root / "AGENTS.md").read_text())
            settings = json.loads((root / ".claude/settings.local.json").read_text())
            self.assertEqual(settings["permissions"], prior["permissions"])
            self.assertEqual(len(settings["hooks"]["PreToolUse"]), 2)
            self.assertEqual(install(root, "test-ws")["files"], [])
            self.assertEqual((root / ".agents/skills/p4-work/SKILL.md").read_bytes(),
                             (root / ".claude/skills/p4-work/SKILL.md").read_bytes())
            with self.assertRaises(HarnessError):
                install(root, "another-ws")

    def test_readonly_existing_instructions_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.md"
            path.write_text("preserve")
            path.chmod(0o444)
            try:
                with self.assertRaises(HarnessError):
                    install(tmp, "ws")
                self.assertEqual(path.read_text(), "preserve")
                self.assertFalse((Path(tmp) / ".p4-harness").exists())
            finally:
                path.chmod(0o644)

    def test_windows_hook_command_handles_spaces_quotes_and_metacharacters(self):
        command = command_string(["C:/Python/python.exe", "D:/tools & more/p4h.py", "a'b $value"], True)
        script = base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le")
        self.assertIn("'D:/tools & more/p4h.py'", script)
        self.assertIn("'a''b $value'", script)


if __name__ == "__main__":
    unittest.main()
