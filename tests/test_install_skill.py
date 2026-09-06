"""Portable installer checks. All installations use temporary directories."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_skill", ROOT / "install_skill.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="photo-installer-test-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in installer.REQUIRED:
            file = self.source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(("fixture: " + name + "\n").encode("utf-8"))
        self.destination = self.root / "codex" / "skills" / "photo-to-blender"

    def tearDown(self):
        self.temporary.cleanup()

    def install(self, destination=None):
        return installer.install(destination or self.destination, source=self.source)

    def link_or_skip(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as exc:
            self.skipTest(f"Creating symlinks is unavailable on this host: {exc}")

    def test_new_destination_installs_verified_files(self):
        result = self.install()
        self.assertEqual(result["status"], "installed")
        self.assertTrue(result["all_file_hashes_match"])
        self.assertEqual(result["files"], len(installer.REQUIRED))
        self.assertEqual(result["installed_path"], str(self.destination.resolve()))
        self.assertEqual(installer.tree_files(self.source), installer.tree_files(self.destination))

    def test_existing_empty_directory_is_allowed(self):
        self.destination.mkdir(parents=True)
        self.assertEqual(self.install()["status"], "installed")

    def test_same_version_is_idempotent_and_not_rewritten(self):
        self.install()
        marker = self.destination / "SKILL.md"
        os.utime(marker, (1000000000, 1000000000))
        previous = marker.stat().st_mtime_ns
        self.assertEqual(self.install()["status"], "already_installed")
        self.assertEqual(marker.stat().st_mtime_ns, previous)

    def test_different_version_refused_without_mutation(self):
        self.install()
        marker = self.destination / "SKILL.md"
        marker.write_bytes(b"existing custom instructions")
        before = installer.tree_files(self.destination)
        with self.assertRaisesRegex(installer.InstallError, "Nothing was overwritten"):
            self.install()
        self.assertEqual(installer.tree_files(self.destination), before)

    def test_extra_noncache_file_is_not_an_identical_version(self):
        self.install()
        extra = self.destination / "private-note.txt"
        extra.write_bytes(b"preserve me")
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(extra.read_bytes(), b"preserve me")

    def test_missing_installed_file_is_not_merged(self):
        self.install()
        missing = self.destination / "SKILL.md"
        missing.unlink()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse(missing.exists())

    def test_custom_final_directory_preserves_all_file_bytes(self):
        (self.source / "说明.txt").write_bytes("原样复制\r\n".encode("utf-8"))
        asset = self.source / "assets" / "sample.bin"
        asset.parent.mkdir()
        asset.write_bytes(bytes(range(256)))
        custom = self.root / "custom skill directory"
        result = self.install(custom)
        self.assertEqual(result["installed_path"], str(custom.resolve()))
        self.assertEqual(installer.tree_files(custom), installer.tree_files(self.source))
        self.assertFalse((custom / "photo-to-blender").exists())

    def test_bytecode_and_cache_directories_are_excluded(self):
        cache = self.source / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "anything.txt").write_bytes(b"cache")
        (self.source / "scripts" / "old.pyc").write_bytes(b"bytecode")
        (self.source / "scripts" / "old.pyo").write_bytes(b"bytecode")
        self.install()
        self.assertFalse((self.destination / "scripts" / "__pycache__").exists())
        self.assertFalse((self.destination / "scripts" / "old.pyc").exists())
        self.assertFalse((self.destination / "scripts" / "old.pyo").exists())

    def test_runtime_cache_does_not_make_installed_skill_different(self):
        self.install()
        cache = self.destination / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "project_workflow.cpython-313.pyc").write_bytes(b"local cache")
        self.assertEqual(self.install()["status"], "already_installed")

    def test_missing_source_requirement_creates_no_destination(self):
        (self.source / "SKILL.md").unlink()
        with self.assertRaisesRegex(installer.InstallError, "Missing required"):
            self.install()
        self.assertFalse(self.destination.parent.exists())

    def test_file_as_destination_is_preserved(self):
        target = self.root / "existing-file"
        target.write_bytes(b"user data")
        with self.assertRaises(installer.InstallError):
            self.install(target)
        self.assertEqual(target.read_bytes(), b"user data")

    def test_source_file_link_cannot_copy_outside_data(self):
        outside = self.root / "private.txt"
        outside.write_bytes(b"not for copying")
        self.link_or_skip(self.source / "linked.txt", outside)
        with self.assertRaisesRegex(installer.InstallError, "Links"):
            self.install()
        self.assertFalse(self.destination.parent.exists())

    def test_source_directory_link_cannot_copy_outside_data(self):
        outside = self.root / "private-folder"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"not for copying")
        self.link_or_skip(self.source / "linked-folder", outside, directory=True)
        with self.assertRaisesRegex(installer.InstallError, "Links"):
            self.install()
        self.assertFalse(self.destination.parent.exists())

    def test_destination_link_is_refused(self):
        real = self.root / "other-folder"
        real.mkdir()
        self.destination.parent.mkdir(parents=True)
        self.link_or_skip(self.destination, real, directory=True)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(list(real.iterdir()), [])

    def test_overlapping_source_and_destination_refused(self):
        nested = self.source / "installed"
        with self.assertRaisesRegex(installer.InstallError, "separate directories"):
            self.install(nested)
        self.assertFalse(nested.exists())

    def test_default_destination_obeys_codex_home(self):
        codex_directory = self.root / "chosen-codex-home"
        with patch.dict(os.environ, {"CODEX_HOME": str(codex_directory)}):
            self.assertEqual(installer.default_destination(),
                             codex_directory / "skills" / "photo-to-blender")
            with patch.object(installer, "SOURCE", self.source):
                result = installer.install()
        self.assertTrue(Path(result["installed_path"]).is_dir())
        self.assertEqual(Path(result["installed_path"]),
                         codex_directory.resolve() / "skills" / "photo-to-blender")

    def test_default_destination_falls_back_to_home(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "home", return_value=self.root):
            self.assertEqual(installer.default_destination(),
                             self.root / ".codex" / "skills" / "photo-to-blender")

    def test_real_bundle_cli_needs_no_machine_validation_files(self):
        custom = self.root / "安装目录"
        result = subprocess.run(
            [sys.executable, str(ROOT / "install_skill.py"), "--destination", str(custom)],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "installed")
        self.assertEqual(installer.tree_files(custom), installer.tree_files(ROOT / "skill"))
        self.assertFalse((custom / "install_skill.py").exists())
        self.assertFalse((custom / "validation").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
