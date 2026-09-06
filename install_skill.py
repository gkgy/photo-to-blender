#!/usr/bin/env python3
"""Install the bundled Codex skill without replacing existing user files.

Only the Python standard library is required. --destination names the final
skill directory, not its parent. To update a different installed version, first
move that directory to a backup location yourself, then run this installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "skill"
REQUIRED = (
    "SKILL.md",
    "agents/openai.yaml",
    "scripts/project_workflow.py",
    "scripts/blender_workflow.py",
    "scripts/deliver_workflow.py",
)


class InstallError(Exception):
    """An installation was refused or did not finish safely."""


def default_destination():
    codex_directory = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return codex_directory.expanduser() / "skills" / "photo-to-blender"


def is_link(path):
    """Also recognize junctions/reparse points on Python 3.11 on Windows."""
    attributes = path.lstat()
    return stat.S_ISLNK(attributes.st_mode) or bool(
        getattr(attributes, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def tree_files(folder):
    """Read regular files without following links or copying Python caches."""
    if is_link(folder) or not folder.is_dir():
        raise InstallError(f"Expected a real directory, not a link: {folder}")
    base = folder.resolve(strict=True)
    contents = {}

    def visit(directory):
        for entry in sorted(directory.iterdir()):
            if is_link(entry):
                raise InstallError(f"Links are not included in skill installations: {entry}")
            if entry.name == "__pycache__" or entry.suffix in {".pyc", ".pyo"}:
                continue
            if not entry.resolve(strict=True).is_relative_to(base):
                raise InstallError(f"Path leaves the skill directory: {entry}")
            if entry.is_dir():
                visit(entry)
            elif entry.is_file():
                contents[entry.relative_to(base).as_posix()] = entry.read_bytes()
            else:
                raise InstallError(f"Only regular skill files are supported: {entry}")

    visit(base)
    return contents


def hashes(contents):
    return {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}


def check_target(target, expected_hashes):
    if target.is_symlink() or (target.exists() and is_link(target)):
        raise InstallError(f"Destination must not be a link or junction: {target}")
    if not target.exists():
        return "new"
    if not target.is_dir():
        raise InstallError(f"Destination already exists and is not a directory: {target}")
    if next(target.iterdir(), None) is None:
        return "empty"
    if hashes(tree_files(target)) == expected_hashes:
        return "identical"
    raise InstallError(
        f"A different skill or extra files already exist at {target}. "
        "Nothing was overwritten. Move this directory to a backup location "
        "or choose a new --destination before installing."
    )


def install(destination=None, *, source=None):
    bundled = Path(source) if source is not None else SOURCE
    bundled = bundled.expanduser().absolute()
    contents = tree_files(bundled)
    missing = [name for name in REQUIRED if name not in contents]
    if missing:
        raise InstallError("Missing required skill files: " + ", ".join(missing))
    expected_hashes = hashes(contents)
    requested = Path(destination if destination is not None else default_destination())
    requested = requested.expanduser().absolute()
    state = check_target(requested, expected_hashes)
    target = requested.resolve()
    bundled = bundled.resolve()
    if target.is_relative_to(bundled) or bundled.is_relative_to(target):
        raise InstallError("Source and destination must be separate directories.")
    if state == "identical":
        return {"status": "already_installed", "installed_path": str(target),
                "files": len(contents), "all_file_hashes_match": True}

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.installing-", dir=target.parent))
    try:
        for relative, data in contents.items():
            copied = staging / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(data)
        if hashes(tree_files(staging)) != expected_hashes:
            raise InstallError("The staged file hashes do not match the bundled skill.")
        # Recheck before committing. Never merge into an existing installation.
        state = check_target(target, expected_hashes)
        if state == "identical":
            return {"status": "already_installed", "installed_path": str(target),
                    "files": len(contents), "all_file_hashes_match": True}
        if state == "empty":
            target.rmdir()  # This operation only succeeds for an empty directory.
        staging.rename(target)
        if hashes(tree_files(target)) != expected_hashes:
            raise InstallError(f"Installed file verification failed; inspect {target}")
        return {"status": "installed", "installed_path": str(target),
                "files": len(contents), "all_file_hashes_match": True}
    finally:
        if staging.exists():
            # Only remove the temporary directory created above, after checking
            # its resolved parent and name. The destination is never deleted.
            if (is_link(staging) or staging.resolve().parent != target.parent.resolve()
                    or not staging.name.startswith(f".{target.name}.installing-")):
                raise InstallError(f"Temporary cleanup boundary check failed: {staging}")
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=None,
                        help="Final skill directory (default: CODEX_HOME/skills/photo-to-blender "
                             "or ~/.codex/skills/photo-to-blender)")
    arguments = parser.parse_args(argv)
    try:
        result = install(arguments.destination)
    except (InstallError, OSError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
