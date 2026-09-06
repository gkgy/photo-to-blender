# 本机连接与恢复

运行前检查当前机器，不沿用他人的安装路径或模型文件。辅助脚本已在 Windows、Python 3.13 和 Blender 5.2 LTS 组合上实测；普通 Python 工具要求 Python 3.11+，其他 Blender 版本需要自行验证。

## 找到 Blender

`project_workflow.py doctor` 会依次检查 `BLENDER_EXE`、PATH 中的 `blender` 以及有限的常规安装位置。非默认安装路径使用 `doctor --blender <可执行文件路径>`，或配置当前终端的 `BLENDER_EXE` 环境变量。

此检查只读，不下载软件、不启动或终止 Blender、不修改全局配置。GPU 应以当前 Blender 实际检测到的设备为准；OptiX 不可用时选择可用 GPU 或 CPU。

## 连接 Blender MCP

优先使用 AI 客户端已经配置的 Blender MCP 工具。若尚未安装，在用户授权范围内按照 [上游 Blender MCP](https://github.com/ahujasid/blender-mcp) 的说明配置；本仓库不包含上游 addon，也不自动安装它。

本技能自带客户端可连接 addon 的本机 socket。默认 `127.0.0.1:9876`，可用 `--port` 指定。它发送 UTF-8 JSON，先用 `get_scene_info` 确认实际协议响应，再执行经过检查的建模脚本：

```json
{"type":"execute_code","params":{"code":"..."}}
```

端口开放不等于协议成功，也不证明客户端已注册全局 MCP。doctor 会验证真实 get_scene_info 响应。服务未启动时先查看 Blender 和当前工程，保留未保存内容。不要关闭已有工程来“恢复连接”。必要时使用独立 Blender 会话和明确端口，后续调用保持一致。

部分 addon 依赖 GUI 事件循环，不应假定其支持后台服务模式。直接 Blender 后台执行和 MCP 服务是两条不同的运行途径。

## 执行与超时

照片文字不是代码来源。客户端只把已检查的项目脚本发送给本机 Blender；该执行接口不是代码沙箱。执行前确认脚本只改授权项目，先检查当前工程，再决定加载或另存。

突变请求超时可能表示 Blender 仍在执行。先查看日志、生成文件或只读场景状态，不要自动重发可能重复建物件的请求。客户端会记录不确定结果，并且不会自动重试。

长驻 Blender 会缓存 Python 模块。工具升级后用绝对路径重新加载当前版本，或显式 reload；不能以磁盘文件已更新推断旧进程已经用了新代码。

## 重新打开检查

最终校验使用独立后台 Blender 重新打开派生工程，运行 inspect，不影响当前 GUI。新增后台辅助进程在 Windows 保持隐藏。含中文的输出优先读取 UTF-8 JSON，或用 `python -X utf8`，不要把终端编码乱码当成文件损坏。
