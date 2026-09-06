# 命令与项目契约

辅助脚本不负责从图片自动推导几何。Agent 完成照片分析、主体建模和视觉判断；脚本负责保留输入、连接、取景起点、渲染和交付。Python 工具仅依赖标准库；`blender_workflow.py` 在 Blender Python 中运行。

以下为 PowerShell 示例。替换项目名和照片路径；变量不覆盖系统 HOME/CODEX_HOME。

```powershell
$workflowCodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$workflowSkill = Join-Path $workflowCodexHome 'skills/photo-to-blender'
$replicaProject = Join-Path $env:USERPROFILE 'Documents/product-replica-001'
python "$workflowSkill/scripts/project_workflow.py" doctor
python "$workflowSkill/scripts/project_workflow.py" init --project $replicaProject --title '产品照片复刻' --reference './input/photo.jpg'
```

多角度原图可重复 `--reference`。已有工程添加 `--blend-source './input/source.blend'`：复制到项目的 `model/model_v001.blend`，`source_blend` 指向该基线，工作输出 `blend_file` 指向 `model/model_v002.blend`。无工程时工作输出为 v001。非空目标会拒绝初始化，续做应直接读取原项目。

## 项目文件

`reference/` 保存原图，`model/` 保存模型版本，`renders/` 保存视图，`reviews/` 保存评估，`logs/` 保存检查与执行记录，`delivery/` 保存按版本生成的交付。`brief.json` 记录照片分析，`project.json` 是工具共享配置。

建好主体后，将 `subject_collection` 设为只包含该物体的集合；`scene` 设为装配场景名称。`unit_scale_length=0.001` 表示 1 Blender 单位按 1 mm 解释，**继承现有工程时改为工程实际值**，不能直接把旧模型单位重设为默认值。

配置渲染清单示例：

```json
{
  "schema_version": 1,
  "title": "产品复刻",
  "unit_scale_length": 0.001,
  "subject_collection": "SUBJECT",
  "scene": "Scene",
  "blend_file": "model/model_v001.blend",
  "render_jobs": [
    {"file":"renders/01_gray.png","label":"结构灰模","scene":"Scene","camera":"WF Hero","width":720,"height":800,"kind":"gray"},
    {"file":"renders/02_hero.png","label":"整体效果","scene":"Scene","camera":"WF Hero","width":1800,"height":2000,"kind":"beauty"},
    {"file":"renders/03_side.png","label":"侧面","scene":"Scene","camera":"WF Side","width":1400,"height":1550,"kind":"beauty"}
  ]
}
```

此片段用于修改初始化结果，保留初始化生成的 `reference_images`、来源哈希等字段。阶段清单文件名不可重复。需要前后对比时可另加 `baseline_image`，值为项目内上一版图片相对路径。

## 在 Blender / MCP 中执行

优先用已可调用的 Blender MCP 执行 Python。也可把以下内容写入项目 `model/run_stage.py`，使用本技能客户端：

```python
import importlib.util, os
from pathlib import Path
skill_scripts = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'skills'/'photo-to-blender'/'scripts'
spec = importlib.util.spec_from_file_location('photo_to_blender_runtime', skill_scripts/'blender_workflow.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
# 当前 Blender 中应已加载本项目的模型；先检查，保护之前未保存的工程。
result = runtime.run(WORKFLOW_PROJECT, 'render', stage='gray', overwrite_derived=True)
print(result)
```

```powershell
python "$workflowSkill/scripts/project_workflow.py" mcp-info
python "$workflowSkill/scripts/project_workflow.py" mcp --project $replicaProject --script "$replicaProject/model/run_stage.py" --timeout 900
```

客户端预置 `WORKFLOW_PROJECT`，固定连接本机，脚本只从项目或本技能 scripts 目录读取。其他 MCP 工具不会自动预置该变量，需在自己的代码中明确项目路径。示例按文件加载当前工具版本，避免长驻 Blender 使用旧模块缓存。不要把整个工具文件当作顶层代码 exec 后又调用函数。

首次需要通用灯光/相机时调用 `run(project, 'prepare')`；它要求已有非空主体集合，保存派生工程，且会把活动相机设为 WF Hero。相机是自动取景的正交起点，之后仍需人工对图校正透视与角度。已有用户灯会保留并报告，因此 prepare 不是清空式布光器。

接续渲染已存在的、由工作流标记的派生文件，使用 `overwrite_derived=True`。该参数只表示本次允许覆盖这个项目派生版本，不允许覆盖 source_blend 或原图。需要保留一个检查点时先把 blend_file 改成下一个未存在版本。

灰模阶段用 `stage='gray'`；结构收敛后用 `stage='beauty'`；`all` 仅在全部视图都需要刷新时使用。默认沿用场景渲染引擎和设备；GPU 选择应以当前 Blender 检测到的设备为准。

## 独立重新打开与打包

```powershell
$workflowBlender = $env:BLENDER_EXE # 设为 doctor 找到的可执行文件
& $workflowBlender --background "$replicaProject/model/model_v001.blend" --python "$workflowSkill/scripts/blender_workflow.py" -- --project $replicaProject --action inspect
python "$workflowSkill/scripts/deliver_workflow.py" --project $replicaProject
```

将示例 blend 路径换成 project.json 中当前 blend_file。inspect 不修改工程。最终交付前先完成真实视觉检查并写 review.json，再运行交付工具；预览或未完成评估时使用 `--draft`，页面会明确标记。

交付工具验证源图哈希、派生工程与检查记录、PNG 尺寸、评估状态，生成原图并排对比页、文件清单和 ZIP。每次生成新目录，不删除旧交付；最新位置记录在 `delivery/latest.json`。文件检查不是相似度评分。
