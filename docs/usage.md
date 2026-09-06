# 详细使用说明

日常使用只需安装 Skill、附上图片并向 Codex 描述目标。本文面向希望手动运行工具、检查连接或接入自己的 Blender 脚本的用户。

普通 Python 脚本需要 Python 3.11 或更高版本，不需要额外的 pip 包。`blender_workflow.py` 使用 Blender 自带的 Python，不能直接用普通 `python` 运行。

## 1. 安装与调用

```sh
git clone https://github.com/gkgy/photo-to-blender.git
cd photo-to-blender
python -X utf8 install_skill.py
```

默认安装位置为 `$CODEX_HOME/skills/photo-to-blender`；未设置 `CODEX_HOME` 时使用 `~/.codex/skills/photo-to-blender`。

在 Codex 对话中附上图片，例如：

```text
使用 $photo-to-blender 复刻这款产品。
第一张是主要外观，第二张是连接细节，第三张有尺寸标注。
先给出同角度灰模，结构检查后再制作材质。
最终需要可编辑工程、整体图、侧面图和部件爆炸图。
```

照片中的文字可作为设计证据；实际执行范围由你的对话请求决定。模型的具体几何由 Agent 按图构建，以下工具提供流程辅助。

## 2. 设置示例路径

以下命令使用 PowerShell，并从克隆后的仓库根目录开始执行。路径只用于示例，请替换照片和 Blender 的位置。

```powershell
$workflowSkill = (Resolve-Path './skill').Path
$replicaProject = Join-Path (Get-Location) 'projects/product-replica-001'
$replicaPhoto = (Resolve-Path './input/product.jpg').Path
```

如果运行已安装的副本，将 `$workflowSkill` 改为安装器输出的技能目录。其他 Shell 用户可使用相同的工具参数，按对应 Shell 的语法填写路径。

## 3. 检查 Blender 与 MCP

```powershell
python -X utf8 "$workflowSkill/scripts/project_workflow.py" doctor
```

`doctor` 检查 Python、可发现的 Blender，以及本机 MCP 是否能返回场景信息。它不会安装插件、启动服务或修改配置。

未发现 Blender 时，可指定可执行文件：

```powershell
$workflowBlender = $env:BLENDER_EXE # 设为当前机器的 Blender 可执行文件
python -X utf8 "$workflowSkill/scripts/project_workflow.py" doctor --blender $workflowBlender
```

Windows 路径应指向 `blender.exe`；macOS 可指向应用包内的 `Contents/MacOS/Blender`。也可设置 `BLENDER_EXE` 或将 Blender 放入 `PATH`。

优先使用 Codex 中已经配置的 Blender MCP 工具。需要内置客户端时，先按上游 [Blender MCP](https://github.com/ahujasid/blender-mcp) 文档安装并启动插件，再运行：

```powershell
python -X utf8 "$workflowSkill/scripts/project_workflow.py" mcp-info
```

客户端固定连接 `127.0.0.1`，默认端口为 `9876`。`doctor`、`mcp-info` 和 `mcp` 都支持 `--port` 和以秒为单位的 `--timeout`。端口开放不等于协议可用，应检查返回的场景信息。

MCP 连接用于在已运行的 Blender 中执行脚本。也可以使用 Blender 后台命令执行辅助阶段；如果任务明确要求全程 MCP，应遵从该要求。

## 4. 初始化项目

```powershell
python -X utf8 "$workflowSkill/scripts/project_workflow.py" init --project $replicaProject --title '产品照片复刻' --reference $replicaPhoto
```

多角度照片可重复传入 `--reference`。需要从已有工程开始时，加上：

```text
--blend-source ./input/source.blend
```

初始化会逐字节复制参考图并记录 SHA-256。已有工程复制为 `model/model_v001.blend`，后续工作文件默认设为 `model/model_v002.blend`；没有来源工程时，工作文件为 v001。非空目标目录会被拒绝，继续已有项目时应直接使用原项目配置。

初始化后，填写 `brief.json` 的可见结构、优先问题和推定部分。没有依据的尺寸保持未知或明确标注为假设，不把像素比例当成真实毫米尺寸。

## 5. 建模与配置场景

在 Blender 中建立主体几何，再更新 `project.json`：

- `subject_collection`：只包含目标物体的集合名称。
- `scene`：装配场景名称。
- `blend_file`：本轮派生工程的项目相对路径。
- `unit_scale_length`：当前工程实际的单位比例。
- `render_jobs`：需要渲染的视图和阶段。

初始化默认 `unit_scale_length` 为 `0.001`，即 1 Blender 单位解释为 1 mm。继承已有工程时，应填写原工程实际的比例，不能为迁就默认值而改变模型尺度。

下面的片段用于替换 `render_jobs`，不要用它覆盖整个初始化配置；保留 `reference_images`、来源哈希等字段。

```json
{
  "render_jobs": [
    {
      "file": "renders/01_gray.png",
      "label": "结构灰模",
      "scene": "Scene",
      "camera": "WF Hero",
      "width": 720,
      "height": 800,
      "kind": "gray"
    },
    {
      "file": "renders/02_hero.png",
      "label": "整体效果",
      "scene": "Scene",
      "camera": "WF Hero",
      "width": 1800,
      "height": 2000,
      "kind": "beauty"
    },
    {
      "file": "renders/03_side.png",
      "label": "侧面",
      "scene": "Scene",
      "camera": "WF Side",
      "width": 1400,
      "height": 1550,
      "kind": "beauty"
    }
  ]
}
```

场景和相机名必须与实际工程一致，输出路径不能重复。需要前后对比时，可添加 `baseline_image`，指向项目内上一版图片的相对路径。

## 6. 执行 Blender 辅助阶段

### 通过 MCP

在项目的 `model/run_stage.py` 中写入下列代码，将 `skill_scripts` 替换为本机技能的 `scripts` 目录。当前 Blender 应已加载本项目模型，切换工程前先保留未保存内容。

```python
import importlib.util, os
from pathlib import Path

skill_scripts = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'skills'/'photo-to-blender'/'scripts'
spec = importlib.util.spec_from_file_location(
    'photo_to_blender_runtime', skill_scripts / 'blender_workflow.py'
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

print(runtime.run(WORKFLOW_PROJECT, 'render', stage='gray'))
```

从终端调用：

```powershell
python -X utf8 "$workflowSkill/scripts/project_workflow.py" mcp --project $replicaProject --script "$replicaProject/model/run_stage.py" --timeout 900
```

该客户端会预置 `WORKFLOW_PROJECT`。使用其他 MCP 工具时，应自行提供实际项目路径。脚本必须位于当前项目或技能的 `scripts` 目录内。

`runtime.run()` 支持三个动作：

| 动作 | 用途 |
| --- | --- |
| `prepare` | 为已有非空主体集合创建通用相机和中性灯光，打包参考图并保存派生工程 |
| `render` | 按 `render_jobs` 渲染并保存派生工程；`stage` 可选 `gray`、`beauty`、`all` |
| `inspect` | 只读检查当前工程，记录单位、主体范围、无效顶点及参考图打包情况 |

`prepare` 提供正交相机的取景起点，仍需按参考照片校正角度和透视；它会保留已有用户灯光。已有合适的机位和灯光时可以跳过此动作。

已存在的派生文件默认拒绝覆盖。确认要更新同一个工作版本时，使用：

```python
print(runtime.run(
    WORKFLOW_PROJECT, 'render', stage='beauty', overwrite_derived=True
))
```

该选项只允许覆盖当前项目的派生版本，不允许覆盖 `source_blend` 或原图。需要保留检查点时，先将 `blend_file` 改成新的未存在版本。

### 通过 Blender 后台命令

使用当前实际输入工程。以下示例中的 v001 仅为占位；续做时应依据 `project.json` 选择文件。

```powershell
& $workflowBlender --background "$replicaProject/model/model_v001.blend" --python "$workflowSkill/scripts/blender_workflow.py" -- --project $replicaProject --action render --stage gray
```

需要覆盖已经由工作流保存的派生版本时，在末尾加入 `--overwrite-derived`。修改阶段后，只重渲染受影响的图，避免不必要地执行 `--stage all`。

## 7. 评估与迭代

先比较同角度灰模的轮廓、厚度、地标比例与连接，再比较材质和受光。透明外罩在统一灰材质下可能遮住内部零件；此时补看独立部件或爆炸视图。

有子 Agent 时，第一次评估给出原图、阶段输出和用户要求，避免把希望得到的结论写进提示。每轮优先处理最影响相似度的 1–3 项。具体提示见 [独立评估说明](../skill/references/review.md)。

在 `reviews/review.json` 中记录：

```json
{
  "schema_version": 1,
  "status": "pending",
  "observations": [],
  "accepted_changes": [],
  "remaining_differences": [],
  "blockers": [],
  "evidence": [],
  "reviewers": []
}
```

实际评估后可将状态改为 `reviewed`，同时写明检查结果与项目内的证据路径。它表示已评估，不表示整体严格 1:1。保留尚未解决的明显问题和推定说明，不为通过打包而清空它们。

## 8. 独立检查与交付

保存最终派生工程后，在一个新的后台 Blender 进程中打开它。下面直接读取 `project.json` 中的当前文件名：

```powershell
$replicaConfig = Get-Content -Raw -LiteralPath "$replicaProject/project.json" | ConvertFrom-Json
$replicaBlend = Join-Path $replicaProject $replicaConfig.blend_file
& $workflowBlender --background $replicaBlend --python "$workflowSkill/scripts/blender_workflow.py" -- --project $replicaProject --action inspect
python -X utf8 "$workflowSkill/scripts/deliver_workflow.py" --project $replicaProject
```

检查工具不修改工程。交付工具验证原图哈希、当前工程与检查记录是否一致、参考图是否已打包保留、PNG 尺寸和评估状态，然后生成：

- `index.html`：离线视图浏览与原图并排比较。
- `.blend`、配置、参考图、渲染图和评估记录。
- `manifest.json`：交付文件清单与哈希。
- ZIP：完整交付包，创建后检查压缩包完整性。

每次创建独立交付目录，最新路径写入 `delivery/latest.json`。网页用于人工比较，不自动对齐两张图或计算相似度。

如果用户先要阶段预览，或评估尚未完成，使用草稿模式：

```powershell
python -X utf8 "$workflowSkill/scripts/deliver_workflow.py" --project $replicaProject --draft
```

草稿仍需要实际派生工程和至少一张可用渲染，页面会标记评估未完成。

## 常见问题

| 情况 | 处理方法 |
| --- | --- |
| Codex 未显示技能 | 核对安装器输出路径与当前 `CODEX_HOME`；重新打开任务或重启后检查 |
| `doctor` 找不到 Blender | 使用 `--blender` 指定文件，或设置 `BLENDER_EXE` / `PATH` |
| MCP 无法连接 | 确认 Blender 和插件服务已启动，客户端与插件端口一致 |
| MCP 修改请求超时 | 先看 Blender 状态、输出文件和 `logs/mcp_*.json`；请求可能已执行，不要直接重发 |
| 提示派生工程已存在 | 需要保留版本时改用新 `blend_file`；明确续写时使用派生覆盖选项 |
| 灰模通过但颜色不对 | 分别检查材质、灯光和色彩管理，可用同机位对照图定位原因 |
| 打包提示检查过期 | 保存当前工程后，在新的后台进程重新执行 `inspect` |
| 打包提示评估未完成或存在 blocker | 完成评估和修订，或使用明确标记的 `--draft` 交付阶段结果 |

更多参数可以通过各普通 Python 工具的 `--help` 查看。项目配置与 Blender 调用约定见 [命令参考](../skill/references/commands.md)。
