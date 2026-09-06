"""Isolated tests: source preservation, overwrite refusal, and uncertain MCP outcomes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("project_workflow", ROOT / "skill/scripts/project_workflow.py")
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


class FakeConnection:
    def __init__(self, chunks=None, error=None):
        self.chunks = list(chunks or [])
        self.error = error
        self.payloads = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, data):
        self.payloads.append(data)

    def recv(self, size):
        if self.error:
            raise self.error
        return self.chunks.pop(0) if self.chunks else b""


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="photo-workflow-test-")
        self.root = Path(self.temporary.name)
        self.reference = self.root / "照片.jpg"
        self.reference.write_bytes(b"\xff\xd8\xff\xe0\x00\x00original-image-bytes\xff\xd9")
        self.project = self.root / "new-project"

    def tearDown(self):
        self.temporary.cleanup()

    def create_project(self, blend=None):
        return workflow.init_project(self.project, [self.reference], "独立测试", blend)

    def script(self):
        script = self.project / "check.py"
        script.write_text("from __future__ import annotations\nassert WORKFLOW_PROJECT\n", encoding="utf-8")
        return script

    def logs(self):
        return [json.loads(path.read_text(encoding="utf-8")) for path in (self.project / "logs").glob("mcp_*.json")]

    def test_reference_bytes_hash_and_pending_review(self):
        original = self.reference.read_bytes()
        self.create_project()
        metadata = json.loads((self.project / "project.json").read_text(encoding="utf-8"))
        item = metadata["reference_images"][0]
        self.assertEqual((self.project / item["path"]).read_bytes(), original)
        self.assertEqual(self.reference.read_bytes(), original)
        self.assertEqual(item["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(item["original_name"], "照片.jpg")
        self.assertEqual(metadata["unit_scale_length"], 0.001)
        self.assertIsNone(metadata["source_blend"])
        self.assertEqual(metadata["blend_file"], "model/model_v001.blend")
        review = json.loads((self.project / "reviews/review.json").read_text())
        self.assertEqual(review["status"], "pending")
        self.assertEqual(review["accepted_changes"], [])

    def test_duplicate_names_preserve_both_sources(self):
        other_folder = self.root / "other"
        other_folder.mkdir()
        second = other_folder / self.reference.name
        second.write_bytes(b"different-source")
        workflow.init_project(self.project, [self.reference, second], "two sources")
        metadata = json.loads((self.project / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(len({item["path"] for item in metadata["reference_images"]}), 2)
        self.assertEqual((self.project / metadata["reference_images"][1]["path"]).read_bytes(), b"different-source")

    def test_missing_reference_creates_nothing(self):
        target = self.root / "not-yet-created" / "project"
        with self.assertRaises(workflow.WorkflowError):
            workflow.init_project(target, [self.reference, self.root / "missing.png"], "bad source")
        self.assertFalse(target.parent.exists())

    def test_missing_blend_creates_nothing(self):
        with self.assertRaises(workflow.WorkflowError):
            self.create_project(self.root / "missing.blend")
        self.assertFalse(self.project.exists())

    def test_nonempty_target_is_never_merged_or_overwritten(self):
        self.project.mkdir()
        sentinel = self.project / "important.txt"
        sentinel.write_bytes(b"user-data")
        with self.assertRaises(workflow.WorkflowError):
            self.create_project()
        self.assertEqual(sentinel.read_bytes(), b"user-data")
        self.assertEqual([item.name for item in self.project.iterdir()], ["important.txt"])

    def test_reinitialization_refused_and_existing_manifest_unchanged(self):
        self.create_project()
        manifest = (self.project / "project.json").read_bytes()
        with self.assertRaises(workflow.WorkflowError):
            self.create_project()
        self.assertEqual((self.project / "project.json").read_bytes(), manifest)

    def test_empty_target_allowed(self):
        self.project.mkdir()
        self.create_project()
        self.assertTrue((self.project / "project.json").is_file())

    def test_blend_source_is_separate_baseline(self):
        blend = self.root / "existing.blend"
        blend.write_bytes(b"BLENDER-v300-source-baseline")
        original = blend.read_bytes()
        self.create_project(blend)
        metadata = json.loads((self.project / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["source_blend"], "model/model_v001.blend")
        self.assertEqual(metadata["blend_file"], "model/model_v002.blend")
        self.assertEqual((self.project / metadata["source_blend"]).read_bytes(), original)
        self.assertFalse((self.project / metadata["blend_file"]).exists())
        self.assertEqual(blend.read_bytes(), original)

    def test_outside_script_rejected_before_connecting(self):
        self.create_project()
        outsider = self.root / "outside.py"
        outsider.write_text("print('outside')", encoding="utf-8")
        with self.assertRaises(workflow.WorkflowError):
            workflow.run_mcp_script(self.project, outsider, connector=lambda *a, **k: self.fail("must not connect"))
        self.assertEqual(self.logs(), [])

    def test_syntax_error_rejected_before_connecting(self):
        self.create_project()
        script = self.script()
        script.write_text("this is not valid python :::", encoding="utf-8")
        with self.assertRaises(workflow.WorkflowError):
            workflow.run_mcp_script(self.project, script, connector=lambda *a, **k: self.fail("must not connect"))
        self.assertEqual(self.logs(), [])

    def test_timeout_is_logged_uncertain_without_retry(self):
        self.create_project()
        script = self.script()
        connection = FakeConnection(error=socket.timeout("simulated timeout"))
        calls = []
        def connector(address, timeout):
            calls.append(address)
            return connection
        with self.assertRaises(workflow.MCPError) as caught:
            workflow.run_mcp_script(self.project, script, timeout=0.1, connector=connector)
        self.assertTrue(caught.exception.may_have_executed)
        self.assertEqual(calls, [("127.0.0.1", 9876)])
        self.assertEqual(len(connection.payloads), 1)
        log = self.logs()[0]
        self.assertEqual(log["status"], "uncertain")
        self.assertTrue(log["may_have_executed"])
        self.assertFalse(log["automatic_retry"])
        self.assertEqual(log["script_sha256"], hashlib.sha256(script.read_bytes()).hexdigest())

    def test_connection_refused_is_logged_not_sent(self):
        self.create_project()
        def connector(*args, **kwargs):
            raise ConnectionRefusedError("simulated refusal")
        with self.assertRaises(workflow.MCPError):
            workflow.run_mcp_script(self.project, self.script(), connector=connector)
        self.assertEqual(self.logs()[0]["status"], "not_sent")
        self.assertFalse(self.logs()[0]["may_have_executed"])

    def test_chunked_unicode_response_and_safe_context(self):
        self.create_project()
        expected = {"status": "success", "result": {"text": "中文响应"}}
        raw = json.dumps(expected, ensure_ascii=False).encode("utf-8")
        connection = FakeConnection(chunks=[raw[:43], raw[43:44], raw[44:]])
        result = workflow.run_mcp_script(self.project, self.script(), connector=lambda *a, **k: connection)
        self.assertEqual(result["response"], expected)
        request = json.loads(connection.payloads[0])
        self.assertEqual(request["type"], "execute_code")
        # The compiled script preserves future imports and receives an exact project path.
        scope = {}
        exec(request["params"]["code"], scope)
        self.assertEqual(scope["_workflow_context"]["WORKFLOW_PROJECT"], str(self.project.resolve()))
        self.assertEqual(self.logs()[0]["status"], "success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
