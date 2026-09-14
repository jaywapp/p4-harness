"""Bounded views must preserve verdicts, evidence and complete change counts."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from p4_harness.checks import verify_required
from p4_harness.common import Config, HarnessError, digest, write_json
from p4_harness.install import install
from p4_harness.reporting import (check_output, failure_excerpt, load_manifest, manifest_file,
                                  page, snapshot_delta, snapshot_output)


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        config = Config(self.root, {})
        self.flow = SimpleNamespace(root=self.root, state=config.control / "state", config=config)

    def snapshot(self, rows):
        value = {"task": "task-1", "change": 42, "environment": {"client": "workspace"},
                 "base_have_id": "baseline", "files": rows}
        value["snapshot_id"] = digest(value)
        write_json(manifest_file(self.flow, value["snapshot_id"]), value)
        return value

    def row(self, path, action="edit", **extra):
        return {"path": path, "action": action, "depot": "//depot/" + path,
                "type": "text", "have_rev": "1", "sha256": "a" * 64, **extra}

    def test_compact_pages_preserve_totals_and_full_manifest(self):
        rows = [self.row(f"src/file-{i:03}.txt", "delete" if i == 199 else "edit") for i in range(200)]
        snap = self.snapshot(rows)
        compact = snapshot_output(self.flow, snap)
        self.assertEqual(compact["file_count"], 200)
        self.assertEqual(compact["actions"], {"edit": 199, "delete": 1})
        self.assertEqual(compact["page"], {"total": 200, "offset": 0, "shown": 10,
                                            "truncated": True, "next_offset": 10})
        final = snapshot_output(self.flow, snap, offset=190)
        self.assertEqual(final["files"][-1]["action"], "delete")
        self.assertIsNone(final["page"]["next_offset"])
        self.assertTrue(final["page"]["truncated"])
        self.assertEqual(snapshot_output(self.flow, snap, full=True), snap)
        self.assertEqual(load_manifest(self.flow, snap["snapshot_id"]), snap)
        # Serialized bytes, NOT a measurement of model tokens, cost or productivity.
        self.assertLess(len(json.dumps(compact).encode()), len(json.dumps(snap).encode()) // 4)

    def test_file_delta_detects_content_action_type_revision_and_missing_entries(self):
        before = self.snapshot([self.row(name) for name in ("same", "content", "action", "type", "rev", "removed")])
        after = self.snapshot([self.row("same"), self.row("content", sha256="b" * 64),
                               self.row("action", "delete", sha256=None), self.row("type", type="text+x"),
                               self.row("rev", have_rev="2"), self.row("new", "add", have_rev=None)])
        result = snapshot_delta(before, after)
        self.assertEqual(result["counts"], {"introduced": 1, "updated": 4, "removed": 1, "unchanged": 1})
        entries = {row["path"]: row for row in result["changes"]}
        self.assertEqual(entries["action"]["after"]["action"], "delete")
        self.assertIsNone(entries["removed"]["after"])
        compact = snapshot_output(self.flow, after, since=before["snapshot_id"], limit=1, offset=4)
        self.assertEqual(compact["counts"], result["counts"])
        self.assertEqual(compact["changes"], [{"path": "rev", "delta": "updated", "action": "edit"}])
        self.assertEqual(compact["page"]["total"], 6)
        full = snapshot_output(self.flow, after, since=before["snapshot_id"], full=True)
        self.assertEqual(full["changes"], result["changes"])

    def test_delta_rejects_unrelated_task_cl_environment_or_baseline(self):
        before = self.snapshot([])
        for field, changed in (("task", "other"), ("change", 43), ("environment", {"client": "other"}),
                               ("base_have_id", "new-baseline")):
            with self.subTest(field=field):
                after = {**before, field: changed}
                with self.assertRaisesRegex(HarnessError, "this task, CL, environment"):
                    snapshot_delta(before, after)

    def test_manifest_id_is_literal_and_contents_are_verified(self):
        snap = self.snapshot([self.row("src/a")])
        for identifier in ("../outside", "a" * 63, "A" * 64, None):
            with self.subTest(identifier=identifier), self.assertRaises(HarnessError):
                load_manifest(self.flow, identifier)
        changed = copy.deepcopy(snap)
        changed["files"][0]["action"] = "delete"
        write_json(manifest_file(self.flow, snap["snapshot_id"]), changed)
        with self.assertRaisesRegex(HarnessError, "contents do not match"):
            load_manifest(self.flow, snap["snapshot_id"])

    def test_invalid_pagination_is_rejected(self):
        for limit, offset in ((0, 0), (201, 0), (10, -1)):
            with self.subTest(limit=limit, offset=offset), self.assertRaises(HarnessError):
                page([], limit, offset)

    def test_failure_excerpt_finds_early_error_retains_full_log_and_marks_omissions(self):
        path = self.root / "build.log"
        data = "compiling\nerror: missing dependency\nlocation: src/a:17\n" + "progress\n" * 5000
        path.write_text(data, encoding="utf-8")
        result = failure_excerpt(path)
        self.assertIn("error: missing dependency", result["excerpt"])
        self.assertEqual(result["selection"], "error_context")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["excerpt"]), 3000)
        self.assertLessEqual(len(result["excerpt"].splitlines()), 24)
        self.assertEqual(path.read_text(encoding="utf-8"), data)

    def test_large_context_lines_do_not_hide_first_diagnostic(self):
        path = self.root / "build.log"
        path.write_text("x" * 10000 + "\n" + "y" * 10000 + "\nerror: actual cause\n", encoding="utf-8")
        result = failure_excerpt(path)
        self.assertIn("error: actual cause", result["excerpt"])
        self.assertTrue(result["truncated"])

    def test_unknown_log_format_falls_back_without_changing_failure_verdict(self):
        path = self.root / "build.log"
        path.write_text("進行中\n" * 100 + "終了コード 7\n", encoding="utf-8")
        diagnostic = failure_excerpt(path)
        record = {"check": "build", "status": "failed", "exit_code": 7, "snapshot_id": "a" * 64,
                  "log": str(path), "diagnostics": diagnostic, "output_tail": "legacy tail"}
        compact = check_output(record)
        self.assertEqual(compact["status"], "failed")
        self.assertEqual(compact["exit_code"], 7)
        self.assertEqual(diagnostic["selection"], "tail_fallback")
        self.assertIn("終了コード 7", diagnostic["excerpt"])

    def test_log_scan_budget_and_duplicate_diagnostics_are_explicit(self):
        path = self.root / "build.log"
        path.write_text("progress\n" * 100 + "error: beyond scan\n", encoding="utf-8")
        result = failure_excerpt(path, scan_bytes=32)
        self.assertEqual(result["scanned_bytes"], 32)
        self.assertEqual(result["matched_lines_in_scan"], 0)
        self.assertIn("beyond scan", result["excerpt"])
        self.assertTrue(result["truncated"])
        path.write_text("error: repeated\n" * 100, encoding="utf-8")
        result = failure_excerpt(path)
        self.assertEqual(result["matched_lines_in_scan"], 100)
        self.assertLess(result["excerpt"].count("repeated"), 100)
        self.assertTrue(result["truncated"])

    def test_success_omits_output_but_full_metadata_is_available(self):
        record = {"check": "smoke", "status": "passed", "exit_code": 0, "snapshot_id": "a" * 64,
                  "log": "/logs/smoke.log", "output_tail": "x" * 4000, "profile_id": "b" * 64}
        compact = check_output(record)
        self.assertNotIn("output_tail", compact)
        self.assertNotIn("diagnostics", compact)
        self.assertEqual(compact["log"], record["log"])
        self.assertEqual(check_output(record, full=True), record)

    def test_project_map_is_operator_owned_across_reinstall(self):
        install(self.root, "workspace")
        path = self.root / ".p4-harness/project-map.md"
        self.assertTrue(path.is_file())
        content = "# Project map\nKeep our entry points and test commands.\n"
        path.write_text(content, encoding="utf-8")
        path.chmod(0o444)
        try:
            self.assertEqual(install(self.root, "workspace")["files"], [])
            self.assertEqual(path.read_text(encoding="utf-8"), content)
        finally:
            path.chmod(0o644)
        self.assertTrue((self.root / ".p4-harness/workflows.md").is_file())

    def test_batch_does_not_report_pass_for_checks_on_different_snapshots(self):
        self.flow.active = Mock(return_value={})
        self.flow.require_owner = Mock()
        self.flow.config.data = {"checks": {name: {} for name in ("one", "two", "three")},
                                 "required_checks": ["one", "two", "three"]}
        with patch("p4_harness.checks.verify", side_effect=[
                {"check": "one", "status": "passed", "snapshot_id": "old"},
                {"check": "two", "status": "passed", "snapshot_id": "new"}]) as run:
            result = verify_required(self.flow, "claude")
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["stale_checks"], ["one"])
        self.assertEqual(result["not_run"], ["three"])
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
