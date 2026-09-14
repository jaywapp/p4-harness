"""Real disposable P4 server tests. Set P4_BIN and P4D_BIN; never use a user's server."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from p4_harness.checks import verify
from p4_harness.cli import apply_ignore, main
from p4_harness.common import Config, HarnessError, p4_literal, write_json
from p4_harness.hooks import run_hook
from p4_harness.install import install
from p4_harness.workflow import Workflow

P4_BIN = os.environ.get("P4_BIN")
P4D_BIN = os.environ.get("P4D_BIN")


@unittest.skipUnless(P4_BIN and P4D_BIN, "Set P4_BIN and P4D_BIN for real-server integration tests")
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4-harness-integration-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.root = self.folder / "workspace with space"
        self.root.mkdir()
        server_root = self.folder / "server"
        server_root.mkdir()
        # Fixture-only bootstrap of a NEW, loopback-only database. Never touches a configured server.
        # P4D 2026.1 defaults to security=4, which requires a pre-created authenticated administrator.
        for setting in ("security=0", "dm.user.noautocreate=0", "dm.user.setinitialpasswd=1", "dm.user.resetpassword=0"):
            subprocess.run([P4D_BIN, "-r", str(server_root), "-cset " + setting], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.address = f"127.0.0.1:{port}"
        self.server = subprocess.Popen([P4D_BIN, "-r", str(server_root), "-p", self.address, "-v", "security=0",
                                        "-L", str(self.folder / "server.log"), "-J", str(self.folder / "journal")],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    self.fail("Disposable p4d did not start")
                time.sleep(0.05)
        self.env_patch = patch.dict(os.environ, {"P4PORT": self.address, "P4USER": "harness-test",
            "P4CLIENT": "workspace", "P4CONFIG": ".unused-test-p4config", "P4IGNORE": ".p4ignore",
            "P4TICKETS": str(self.folder / "tickets"), "P4ENVIRO": str(self.folder / "enviro")})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.raw("client", "-i", input=f"Client: workspace\nRoot: {self.root}\nOptions: noallwrite noclobber nocompress unlocked nomodtime normdir\nView:\n\t//depot/... //workspace/...\n")
        (self.root / "src").mkdir()
        (self.root / "other").mkdir()
        for name in ("main.txt", "delete.txt", "move.txt", "space 한글.txt"):
            (self.root / "src" / name).write_text("original\n", encoding="utf-8")
        (self.root / "other/user.txt").write_text("user baseline\n")
        self.raw("add", str(self.root / "src" / "..."), str(self.root / "other" / "..."))
        self.raw("submit", "-d", "test fixture baseline")
        install(self.root, "workspace", self.address, "harness-test", P4_BIN)
        self.config = Config.load(self.root)
        apply_ignore(self.config)
        self.flow = Workflow(self.config)

    def raw(self, *args, input=None, client=None, user=None):
        command = [P4_BIN, "-p", self.address, "-u", user or "harness-test", "-c", client or "workspace", *args]
        result = subprocess.run(command, input=input, text=True, encoding="utf-8", stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=self.root, timeout=15)
        if result.returncode:
            self.fail(f"Fixture command failed: {args}: {result.stderr} {result.stdout}")
        return result.stdout

    def stop_server(self):
        self.server.terminate()
        try:
            self.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(timeout=5)
        # p4 checkout fixtures are read-only on Windows; TemporaryDirectory needs write access.
        for path in self.folder.rglob("*"):
            if path.is_file():
                path.chmod(0o600)

    def begin(self, scope="src"):
        return self.flow.begin("task-1", "Change behavior", [scope], "claude")

    def configure_check(self, code="print('verified')"):
        self.config.data["checks"] = {"smoke": {"argv": [sys.executable, "-c", code], "timeout_seconds": 10}}
        self.config.data["required_checks"] = ["smoke"]

    def edit(self, text="changed\n", agent="claude", session="claude-session"):
        run_hook(self.flow, agent, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                                 "session_id": session, "tool_input": {"file_path": str(self.root / "src/main.txt")}})
        (self.root / "src/main.txt").write_text(text)

    def test_claude_to_codex_handoff_and_verified_report(self):
        self.begin()
        self.edit()
        with self.assertRaisesRegex(HarnessError, "belongs to claude"):
            self.edit("other", agent="codex", session="codex-session")
        self.flow.handoff("codex", "Review and complete", "claude")
        event = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "session_id": "codex-session",
                 "tool_input": {"command": "*** Begin Patch\n*** Update File: src/main.txt\n@@\n-changed\n+reviewed\n*** End Patch"}}
        self.assertEqual(run_hook(self.flow, "codex", event), {})
        (self.root / "src/main.txt").write_text("reviewed\n")
        with self.assertRaises(HarnessError):
            self.edit()
        self.configure_check()
        self.assertEqual(verify(self.flow, "smoke", "codex")["status"], "passed")
        result = self.flow.finish("codex")
        self.assertEqual(result["verification"], "passed")
        self.assertFalse(result["submitted"])
        self.assertIn("pending", self.raw("change", "-o", str(result["change"])))
        self.assertIsNone(self.flow.active(False))

    def test_existing_changes_and_out_of_scope_edits_are_preserved(self):
        self.raw("edit", str(self.root / "other/user.txt"))
        (self.root / "other/user.txt").write_text("user unsubmitted change\n")
        self.begin()
        with self.assertRaises(HarnessError):
            self.flow.prepare(["other/user.txt"], "claude")
        self.edit()
        self.flow.finish("claude")
        self.assertEqual((self.root / "other/user.txt").read_text(), "user unsubmitted change\n")

    def test_dirty_scope_is_refused_without_reverting(self):
        self.raw("edit", str(self.root / "src/main.txt"))
        (self.root / "src/main.txt").write_text("existing work\n")
        with self.assertRaisesRegex(HarnessError, "existing opened/offline"):
            self.begin()
        self.assertEqual((self.root / "src/main.txt").read_text(), "existing work\n")

    def test_new_delete_and_move_actions_are_in_manifest(self):
        self.begin()
        self.flow.prepare(["src/new.txt"], "claude")
        (self.root / "src/new.txt").write_text("new content\n")
        self.flow.delete("src/delete.txt", "claude")
        self.flow.move("src/move.txt", "src/renamed.txt", "claude")
        snap = self.flow.collect("claude")
        actions = {x["path"]: x["action"] for x in snap["files"]}
        self.assertEqual(actions["src/new.txt"], "add")
        self.assertEqual(actions["src/delete.txt"], "delete")
        self.assertEqual(actions["src/move.txt"], "move/delete")
        self.assertEqual(actions["src/renamed.txt"], "move/add")

    def test_finish_refuses_stale_validation_and_failed_check(self):
        self.begin()
        self.edit()
        self.configure_check()
        verify(self.flow, "smoke", "claude")
        (self.root / "src/main.txt").write_text("changed after verification\n")
        with self.assertRaisesRegex(HarnessError, "stale"):
            self.flow.finish("claude")
        self.configure_check("raise SystemExit(3)")
        self.assertEqual(verify(self.flow, "smoke", "claude")["status"], "failed")
        with self.assertRaises(HarnessError):
            self.flow.finish("claude")

    def test_changed_source_during_verification_is_stale(self):
        self.begin()
        self.edit()
        self.configure_check("from pathlib import Path; Path('src/main.txt').write_text('changed by check')")
        self.assertEqual(verify(self.flow, "smoke", "claude")["status"], "stale")

    def test_shelf_and_pause_resume_preserve_pending_work(self):
        self.begin()
        self.edit()
        shelf = self.flow.shelve("claude")
        self.assertTrue(shelf["snapshot_id"])
        self.assertIn("shelved", self.raw("describe", "-S", "-s", str(shelf["change"])).lower())
        self.flow.pause("continue tomorrow", "claude")
        with self.assertRaises(HarnessError):
            self.flow.prepare(["src/main.txt"], "claude")
        self.flow.resume("codex")
        self.assertEqual(self.flow.active()["owner"], "codex")
        self.assertEqual((self.root / "src/main.txt").read_text(), "changed\n")

    def test_no_configured_checks_is_not_reported_as_passed(self):
        self.begin()
        self.edit()
        self.assertEqual(self.flow.finish("claude")["verification"], "not_configured")

    def test_wrong_root_is_refused_before_checkout(self):
        self.config.root = self.root / "src"
        with self.assertRaisesRegex(HarnessError, "root"):
            Workflow(self.config).doctor()

    def test_new_file_not_reserved_by_task_blocks_finish(self):
        self.begin()
        (self.root / "src/unowned.txt").write_text("keep this\n")
        with self.assertRaisesRegex(HarnessError, "Unregistered"):
            self.flow.finish("claude")
        self.assertEqual((self.root / "src/unowned.txt").read_text(), "keep this\n")

    def test_same_provider_second_session_cannot_edit(self):
        self.begin()
        self.edit()
        with self.assertRaisesRegex(HarnessError, "Another session"):
            self.edit(session="second-claude")

    def test_installed_metadata_is_ignored_even_for_root_scope(self):
        self.begin(".")
        self.edit()
        self.assertEqual(len(self.flow.collect("claude")["files"]), 1)

    def test_unicode_and_spaces_in_file_paths(self):
        self.begin()
        self.flow.prepare(["src/space 한글.txt"], "claude")
        (self.root / "src/space 한글.txt").write_text("한글 내용\n", encoding="utf-8")
        self.assertEqual(self.flow.collect("claude")["files"][0]["path"], "src/space 한글.txt")

    def test_p4config_ignore_precedence_preserves_user_rules(self):
        (self.root / ".unused-test-p4config").write_text("P4IGNORE=custom-ignore\n")
        (self.root / "custom-ignore").write_text("*.generated\ncustom-ignore\n")
        (self.root / "src/cache.generated").write_text("build output")
        os.environ.pop("P4IGNORE", None)
        apply_ignore(self.config)
        self.assertIn("custom-ignore", os.environ["P4IGNORE"])
        self.begin(".")
        self.edit()
        self.assertEqual(len(self.flow.collect("claude")["files"]), 1)

    def test_ignore_setting_without_environment_and_cli_json_hook(self):
        os.environ.pop("P4IGNORE", None)
        runner = Path(__file__).resolve().parents[1] / "p4h.py"
        command = [sys.executable, str(runner), "--workspace", str(self.root)]
        result = subprocess.run([*command, "doctor"], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.begin()
        event = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "session_id": "codex-session",
                 "tool_input": {"command": "*** Begin Patch\n*** Update File: src/main.txt\n*** End Patch"}}
        result = subprocess.run([*command, "hook", "--agent", "codex"], input=json.dumps(event),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("edit", self.raw("opened"))

    def test_special_new_and_tracked_names_are_literal(self):
        path = self.root / "src/literal#@%.txt"
        path.write_text("literal baseline\n")
        self.raw("add", "-f", str(path))
        self.raw("submit", "-d", "literal filename fixture")
        self.begin()
        self.flow.prepare([str(path)], "claude")
        path.write_text("literal edit\n")
        self.assertEqual(self.flow.collect("claude")["files"][0]["path"], "src/literal#@%.txt")
        self.flow.prepare(["src/new#name.txt"], "claude")
        (self.root / "src/new#name.txt").write_text("new literal file")
        paths = {row["path"]: row["action"] for row in self.flow.collect("claude")["files"]}
        self.assertEqual(paths["src/new#name.txt"], "add")

    def test_validation_timeout_blocks_finish(self):
        self.begin()
        self.edit()
        self.configure_check("import time; time.sleep(10)")
        self.config.data["checks"]["smoke"]["timeout_seconds"] = 0.1
        self.assertEqual(verify(self.flow, "smoke", "claude")["status"], "timeout")
        with self.assertRaises(HarnessError):
            self.flow.finish("claude")

    def test_changed_have_revision_and_other_cl_are_refused(self):
        self.begin()
        self.raw("sync", str(self.root / "src/main.txt") + "#none")
        with self.assertRaisesRegex(HarnessError, "baseline revisions"):
            self.flow.collect("claude")
        self.raw("sync", str(self.root / "src/main.txt"))
        self.raw("edit", str(self.root / "src/main.txt"))
        with self.assertRaisesRegex(HarnessError, "another CL"):
            self.flow.prepare(["src/main.txt"], "claude")

    def test_hook_relative_patch_uses_event_cwd(self):
        self.begin()
        event = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "session_id": "claude-session",
                 "cwd": str(self.root / "src"),
                 "tool_input": {"command": "*** Begin Patch\n*** Update File: main.txt\n*** End Patch"}}
        self.assertEqual(run_hook(self.flow, "claude", event), {})
        self.assertIn("src/main.txt", self.flow.active()["prepared"])

    def test_exclusive_checkout_conflict_does_not_grant_edit(self):
        self.raw("edit", "-t", "text+l", str(self.root / "src/main.txt"))
        self.raw("submit", "-d", "exclusive fixture")
        other = self.folder / "other-workspace"
        other.mkdir()
        self.raw("client", "-i", client="other", input=f"Client: other\nRoot: {other}\nView:\n\t//depot/... //other/...\n")
        self.raw("sync", "//depot/src/main.txt", client="other")
        self.raw("edit", "//depot/src/main.txt", client="other")
        self.begin()
        with self.assertRaises(HarnessError):
            self.flow.prepare(["src/main.txt"], "claude")
        self.assertNotIn("src/main.txt", self.flow.active()["prepared"])
        self.assertEqual((self.root / "src/main.txt").read_text(), "original\n")


if __name__ == "__main__":
    unittest.main()
