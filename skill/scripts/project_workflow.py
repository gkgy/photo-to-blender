#!/usr/bin/env python3
"""Create photo reconstruction projects and talk to a local Blender MCP addon.

Standard library only. No service installation, configuration changes, or
automatic retries of Blender execution are performed by this tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid


SCHEMA_VERSION = 1
HOST = "127.0.0.1"
SCRIPT_ROOT = Path(__file__).resolve().parent
DIRECTORIES = ("reference", "model", "renders", "reviews", "logs", "delivery")
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class WorkflowError(Exception):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


class MCPError(WorkflowError):
    def __init__(self, message, *, may_have_executed=False, details=None):
        super().__init__(message, details=details)
        self.may_have_executed = may_have_executed


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, value):
    """Replace a tool-owned JSON log atomically, retaining real UTF-8 text."""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def checked_file(value, label):
    source = Path(value).expanduser()
    try:
        source = source.resolve(strict=True)
        if not source.is_file():
            raise WorkflowError(f"{label}不是文件：{source}")
        with source.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise WorkflowError(f"无法读取{label}：{value}。{exc}") from exc
    return source


def check_destination(target):
    if target.is_symlink():
        raise WorkflowError(f"项目目标不能是符号链接：{target}")
    if target.exists():
        if not target.is_dir():
            raise WorkflowError(f"项目目标已存在且不是目录：{target}")
        if next(target.iterdir(), None) is not None:
            raise WorkflowError(f"项目目录非空，已拒绝覆盖：{target}。请选择新的项目目录。")


def init_project(project, references, title, blend_source=None):
    if not str(title).strip():
        raise WorkflowError("项目标题不能为空。")
    if not references:
        raise WorkflowError("至少需要一张参考照片。")
    # Validate and read every reference before creating any destination paths.
    sources = [checked_file(value, "参考照片") for value in references]
    blend = checked_file(blend_source, "Blender 源工程") if blend_source else None
    reference_data = []
    for index, source in enumerate(sources, 1):
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise WorkflowError(f"无法完整读取参考照片：{source}。{exc}") from exc
        reference_data.append((source, f"reference/{index:02d}_{source.name}", data))

    requested = Path(project).expanduser().absolute()
    check_destination(requested)
    target = requested.resolve()
    check_destination(target)
    existed_empty = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.initializing-", dir=target.parent))
    try:
        for folder in DIRECTORIES:
            (staging / folder).mkdir()
        images = []
        for source, relative, data in reference_data:
            (staging / relative).write_bytes(data)
            images.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(),
                           "original_name": source.name})
        if blend:
            shutil.copy2(blend, staging / "model/model_v001.blend")
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "title": str(title).strip(),
            "created_utc": utc_now(),
            "reference_images": images,
            "unit_scale_length": 0.001,
            "subject_collection": "SUBJECT",
            "scene": "Scene",
            "source_blend": "model/model_v001.blend" if blend else None,
            "blend_file": "model/model_v002.blend" if blend else "model/model_v001.blend",
            "render_jobs": [],
        }
        write_json(staging / "project.json", metadata)
        write_json(staging / "brief.json", {
            "schema_version": SCHEMA_VERSION,
            "status": "draft",
            "subject": "",
            "known_dimensions_mm": [],
            "silhouette_landmarks_normalized": [],
            "visible_structure": [],
            "inferred_structure": [],
            "priorities": [],
            "target_views": [],
            "notes": [],
            "field_guide": {
                "known_dimensions_mm": "每项填写 name、value_mm、reference_path、evidence；没有证据时留空。",
                "silhouette_landmarks_normalized": "每项填写 name、reference_path、x、y；以原图左上角为 (0,0)，右下角为 (1,1)。",
                "visible_structure": "每项填写 part、observation、reference_path，记录照片直接可见的事实。",
                "inferred_structure": "每项填写 part、assumption、reason、confidence，明确哪些结构来自推定。",
                "priorities": "按顺序填写本次最影响相似度的轮廓、厚度、接合、材质或视角问题。",
            },
        })
        write_json(staging / "reviews/review.json", {
            "schema_version": SCHEMA_VERSION, "status": "pending",
            "observations": [], "accepted_changes": [], "remaining_differences": [],
        })
        # Recheck at commit time; never merge into an existing project.
        check_destination(target)
        if target.exists():
            target.rmdir()  # Only an empty directory can be removed.
        staging.rename(target)
    except Exception:
        if staging.exists():
            # This directory was freshly created by mkdtemp under target.parent.
            if staging.resolve().parent != target.parent.resolve() or not staging.name.startswith(f".{target.name}.initializing-"):
                raise WorkflowError("初始化暂存目录的边界检查失败；未执行清理。")
            shutil.rmtree(staging)
        if existed_empty and not target.exists():
            target.mkdir()
        raise
    return {"status": "created", "project": str(target), "reference_count": len(images),
            "blend_copied": bool(blend), "next_step": "填写 brief.json，先校准灰模与相机，再评估材质。"}


def check_port(port):
    if not 1 <= int(port) <= 65535:
        raise WorkflowError("端口必须在 1 到 65535 之间。")
    return int(port)


def local_request(command, *, port=9876, timeout=30, connector=None):
    """One request, one connection, no retry. Blender MCP uses an unframed JSON reply."""
    port = check_port(port)
    if timeout <= 0:
        raise WorkflowError("超时秒数必须大于 0。")
    connector = connector or socket.create_connection
    submitted = False
    try:
        with connector((HOST, port), timeout=min(timeout, 10)) as connection:
            connection.settimeout(timeout)
            payload = json.dumps(command, ensure_ascii=False).encode("utf-8")
            # A failed sendall can still have transmitted part or all of the request.
            submitted = True
            connection.sendall(payload)
            response = bytearray()
            while True:
                block = connection.recv(65536)
                if not block:
                    raise MCPError("Blender MCP 在完整响应前关闭了连接。", may_have_executed=submitted)
                response.extend(block)
                if len(response) > MAX_RESPONSE_BYTES:
                    raise MCPError("Blender MCP 响应超过 64 MiB 上限。", may_have_executed=submitted)
                try:
                    result = json.loads(response)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(result, dict):
                    raise MCPError("Blender MCP 返回的 JSON 不是对象。", may_have_executed=submitted)
                return result
    except MCPError:
        raise
    except (OSError, TimeoutError) as exc:
        if submitted:
            message = (f"本机 Blender MCP 响应中断或超时：{exc}。请求可能仍在执行或已经部分执行；"
                       "不要自动重试，请先查看 Blender 状态与操作日志。")
        else:
            message = (f"无法连接本机 Blender MCP（{HOST}:{port}）：{exc}。请确认 Blender 已打开，"
                       "MCP 插件已启动，且端口一致；本工具不会安装或启停服务。")
        raise MCPError(message, may_have_executed=submitted) from exc


def blender_candidates(explicit=None):
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        if os.environ.get("BLENDER_EXE"):
            candidates.append(Path(os.environ["BLENDER_EXE"]))
        found = shutil.which("blender")
        if found:
            candidates.append(Path(found))
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            folder = os.environ.get(env)
            if folder:
                foundation = Path(folder) / "Blender Foundation"
                if foundation.is_dir():
                    candidates.extend(sorted(foundation.glob("Blender */blender.exe")))
        if sys.platform != "win32":
            candidates.extend([Path("/usr/bin/blender"), Path("/Applications/Blender.app/Contents/MacOS/Blender")])
    unique = []
    seen = set()
    for candidate in candidates:
        key = str(candidate.absolute()).casefold() if os.name == "nt" else str(candidate.absolute())
        if key not in seen:
            unique.append(candidate.absolute())
            seen.add(key)
    return unique


def mcp_info(*, port=9876, timeout=10):
    try:
        response = local_request({"type": "get_scene_info", "params": {}}, port=port, timeout=timeout)
        return {"status": "ok" if response.get("status") == "success" else "server_error",
                "host": HOST, "port": port, "response": response}
    except MCPError as exc:
        return {"status": "unavailable", "host": HOST, "port": port, "error": str(exc)}


def doctor(*, blender=None, port=9876, timeout=10):
    paths = blender_candidates(blender)
    found = [path for path in paths if path.is_file()]
    blender_report = {"status": "found" if found else "not_found",
                      "candidates": [{"path": str(path), "exists": path.is_file()} for path in paths]}
    if found:
        executable = found[0]
        blender_report["executable"] = str(executable)
        try:
            result = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=12,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            blender_report["version"] = result.stdout.splitlines()[0] if result.stdout else ""
            blender_report["version_exit_code"] = result.returncode
            if result.returncode:
                blender_report["error"] = result.stderr[:1000]
        except (OSError, subprocess.TimeoutExpired) as exc:
            blender_report["error"] = str(exc)
    else:
        blender_report["hint"] = "未在有限候选位置找到 Blender；可用 --blender 指定 blender.exe。"
    return {"checked_utc": utc_now(), "read_only": True,
            "python": {"executable": sys.executable, "version": sys.version.split()[0]},
            "blender": blender_report, "mcp": mcp_info(port=port, timeout=timeout)}


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def checked_project(project):
    root = Path(project).expanduser().resolve()
    manifest = checked_file(root / "project.json", "项目清单")
    if not inside(manifest, root):
        raise WorkflowError("project.json 必须位于项目内。")
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"无法解析项目清单：{exc}") from exc
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise WorkflowError("不支持的 project.json schema_version。")
    return root


def run_mcp_script(project, script, *, port=9876, timeout=1800, connector=None):
    port = check_port(port)
    if timeout <= 0:
        raise WorkflowError("超时秒数必须大于 0。")
    root = checked_project(project)
    source = checked_file(script, "执行脚本")
    if not (inside(source, root) or inside(source, SCRIPT_ROOT)):
        raise WorkflowError("脚本必须位于当前项目内或本工作流的 skill/scripts 目录内。")
    if source.suffix.lower() != ".py":
        raise WorkflowError("执行脚本必须是 .py 文件。")
    try:
        data = source.read_bytes()
        script_text = data.decode("utf-8-sig")
        compile(script_text, str(source), "exec")
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise WorkflowError(f"执行前脚本检查失败，尚未发送到 Blender：{exc}") from exc
    context = {"WORKFLOW_PROJECT": str(root), "__file__": str(source), "__name__": "__main__",
               "code": script_text}
    encoded = json.dumps(context, ensure_ascii=True)
    code = ("import json as _workflow_json\n"
            f"_workflow_context = _workflow_json.loads({encoded!r})\n"
            "exec(compile(_workflow_context.pop('code'), _workflow_context['__file__'], 'exec'), _workflow_context)\n")
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    if not inside(logs.resolve(), root):
        raise WorkflowError("logs 目录不能指向项目外。")
    operation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    log_path = logs / f"mcp_{operation_id}.json"
    record = {"schema_version": SCHEMA_VERSION, "operation_id": operation_id,
              "started_utc": utc_now(), "status": "prepared", "host": HOST, "port": check_port(port),
              "request_type": "execute_code", "script": str(source),
              "script_sha256": hashlib.sha256(data).hexdigest(), "timeout_seconds": timeout,
              "automatic_retry": False, "may_have_executed": False}
    write_json(log_path, record)
    try:
        record.update(status="awaiting_response", may_have_executed=True)
        write_json(log_path, record)
        response = local_request({"type": "execute_code", "params": {"code": code}},
                                 port=port, timeout=timeout, connector=connector)
        record.update(finished_utc=utc_now(), response=response)
        if response.get("status") == "success":
            record.update(status="success", may_have_executed=True)
        else:
            record.update(status="server_error", may_have_executed=True,
                          guidance="Blender 报错不代表没有修改场景；检查日志和当前场景后再决定下一步，不自动重试。")
        write_json(log_path, record)
        if record["status"] != "success":
            raise WorkflowError("Blender MCP 返回错误，操作日志已保留；请检查场景后再决定是否重试。",
                                details={"log": str(log_path), "response": response, "may_have_executed": True})
        return {"status": "success", "log": str(log_path), "response": response}
    except MCPError as exc:
        record.update(finished_utc=utc_now(), status="uncertain" if exc.may_have_executed else "not_sent",
                      may_have_executed=exc.may_have_executed, error=str(exc),
                      guidance="不得自动重试。先检查 Blender 当前状态、生成文件和操作日志。" if exc.may_have_executed
                      else "确认本机 MCP 插件服务与端口后重新发起操作。")
        write_json(log_path, record)
        exc.details.update(log=str(log_path), may_have_executed=exc.may_have_executed)
        raise


def build_parser():
    parser = argparse.ArgumentParser(description="照片复刻项目与本机 Blender MCP 工具（标准库，无自动安装或重试）。")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="建立独立项目，逐字节保留参考照片")
    init.add_argument("--project", required=True)
    init.add_argument("--reference", action="append", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--blend-source")
    check = commands.add_parser("doctor", help="只读检测 Python、Blender 与本机 MCP")
    check.add_argument("--blender")
    check.add_argument("--port", type=int, default=9876)
    check.add_argument("--timeout", type=float, default=10)
    info = commands.add_parser("mcp-info", help="只读获取当前 Blender 场景信息")
    info.add_argument("--port", type=int, default=9876)
    info.add_argument("--timeout", type=float, default=10)
    execute = commands.add_parser("mcp", help="发送项目内或工作流内的 Python 脚本；突变请求绝不自动重试")
    execute.add_argument("--project", required=True)
    execute.add_argument("--script", required=True)
    execute.add_argument("--port", type=int, default=9876)
    execute.add_argument("--timeout", type=float, default=1800)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = init_project(args.project, args.reference, args.title, args.blend_source)
        elif args.command == "doctor":
            result = doctor(blender=args.blender, port=args.port, timeout=args.timeout)
        elif args.command == "mcp-info":
            result = mcp_info(port=args.port, timeout=args.timeout)
        else:
            result = run_mcp_script(args.project, args.script, port=args.port, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in ("unavailable", "server_error") else 2
    except (WorkflowError, OSError) as exc:
        result = {"status": "error", "error": str(exc), **getattr(exc, "details", {})}
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
