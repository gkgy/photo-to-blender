# Photo to Blender · 照片复刻工作流

把产品照片或设计截图作为参考，在本机 Blender 中制作可编辑三维模型，并逐轮对照修订。

这是一个供 **Codex 调用的 Skill 和本地辅助工具集**。AI 负责理解图片、编写具体建模逻辑和判断视觉差异；工具负责项目初始化、连接 Blender、分阶段渲染、工程检查与交付打包。

## 能做什么

- 从产品照片、概念设计图或多视角截图开始建模。
- 对照新参考图修订已有 `.blend` 工程，保留来源版本。
- 先检查灰模轮廓和连接结构，再调整材质、颜色与灯光。
- 在支持子 Agent 的环境中安排独立结构评估和材质评估。
- 交付可编辑工程、所需视图、原图对比页、评估记录和 ZIP。

适合有明确轮廓、部件和连接关系的产品，例如耳机、音箱、小型设备与外壳设计。具体几何需要按新图片制作；工作流不会把任意照片自动转换成完整模型。

## 环境

| 项目 | 要求或说明 |
| --- | --- |
| Codex | 能使用本地 Skill、执行工具并读取图片的环境 |
| Python | 3.11 或更高；普通 Python 工具仅依赖标准库 |
| Blender | 已在 Windows + Blender 5.2 环境验证；其他版本和系统需自行验证 |
| Blender Python | `blender_workflow.py` 必须在 Blender 内运行，使用其自带的 `bpy` |
| Blender MCP | 优先使用已配置的连接；也可通过内置客户端连接本机已启动的服务 |
| GPU | 可选；使用当前 Blender 能识别的设备，也可使用 CPU |

本仓库不捆绑或自动安装 Blender MCP 插件。需要连接时，参阅上游 [Blender MCP](https://github.com/ahujasid/blender-mcp) 的安装说明。

## 安装

```sh
git clone https://github.com/gkgy/photo-to-blender.git
cd photo-to-blender
python -X utf8 install_skill.py
```

安装器默认将 `skill/` 安装到 `$CODEX_HOME/skills/photo-to-blender`；未设置 `CODEX_HOME` 时使用用户目录下的 `.codex/skills/photo-to-blender`。安装后，在能加载该技能的 Codex 任务中调用它。如果当前任务尚未显示新技能，重新打开任务或重启 Codex 后再试。

重复安装相同版本会校验并保留现有文件；不同版本不会自动覆盖。更新时，先将原技能目录移到备份位置，再运行安装器。需要自定义位置时使用 `--destination`，该参数指向最终技能目录。

## 开始使用

把照片附到 Codex 对话中，并发送：

```text
用 $photo-to-blender 根据这张照片制作可编辑的 Blender 模型。
先对齐参考机位和灰模，再检查结构、材质与颜色。
需要整体效果图、正面和侧面图；保留原图并输出工程与对比页。
```

如果有多张图，把主参考图、细节图、尺寸标注一起提供。已有模型也可以直接续做：

```text
用 $photo-to-blender 对照新照片修订这个 Blender 工程。
重点检查外壳厚度和连接位置，保留原版，另存新版并展示前后对比。
```

不必自己填写所有配置或运行下面的工具；Skill 会指导 Agent 完成这些步骤。需要手动运行、排查连接或接入自己的脚本时，阅读 [详细使用说明](docs/usage.md)。

## 工作过程

1. **照片分析**：记录轮廓、比例、可见结构、材质与遮挡，区分标注尺寸和推定值。
2. **相机与灰模**：建立主要部件，使用接近参考图的机位检查形状。
3. **结构评估**：优先修正影响最大的轮廓、厚度、孔洞和接合问题。
4. **材质与受光**：分别处理布面、软垫、塑料、金属和透明件，必要时做对照渲染。
5. **复核与交付**：保存新版，在独立 Blender 进程中重新打开检查，生成图示与交付包。

有可用子 Agent 时，评估者独立查看原图和阶段输出；没有子 Agent 时，由主 Agent 分轮检查并如实记录。程序检查与视觉评估分别记录，不用文件检查结果代替相似度结论。

## 输出

每个项目使用独立目录：

```text
product-replica/
├── project.json        # 场景、模型版本、渲染清单
├── brief.json          # 照片分析与推定说明
├── reference/          # 保留原始字节的参考图
├── model/              # Blender 工程与项目脚本
├── renders/            # 灰模、整体效果和所需视图
├── reviews/            # 评估记录与剩余差异
├── logs/               # 执行和工程检查记录
└── delivery/           # 分版本生成的离线对比页和 ZIP
```

视图按任务需要安排，爆炸图、剖切图和细节图可加入渲染清单。每次打包生成新目录，最新交付位置记录在 `delivery/latest.json`。

## 能力边界

- 单张照片通常无法确定真实尺寸、背面和内部结构；缺少证据的部分会标记为推定。
- “外观接近参考图”与“工程尺寸完全还原”是不同验收目标。没有测量依据时，不承诺严格 1:1。
- 相机、几何、材质和色彩管理都可能造成差异，需要分阶段比较。
- 新物体仍需建模与评估，不保证固定耗时，也不保证所有 Blender 版本兼容。

## 仓库内容

| 路径 | 用途 |
| --- | --- |
| [`skill/SKILL.md`](skill/SKILL.md) | Agent 的执行流程与交付要求 |
| [`skill/scripts/project_workflow.py`](skill/scripts/project_workflow.py) | 初始化、环境诊断和本机 MCP 客户端 |
| [`skill/scripts/blender_workflow.py`](skill/scripts/blender_workflow.py) | 场景准备、渲染和只读工程检查 |
| [`skill/scripts/deliver_workflow.py`](skill/scripts/deliver_workflow.py) | 对比页、文件清单和 ZIP |
| [`docs/usage.md`](docs/usage.md) | 手动使用与常见问题 |
| [`skill/references/review.md`](skill/references/review.md) | 独立评估方法与记录格式 |

## 检查工具

```sh
python -X utf8 -m unittest discover -s tests -v
```

测试覆盖项目初始化、来源保留、安装保护和 MCP 不确定结果处理，不需要启动 Blender 或网络服务。GitHub Actions 在 Windows / Ubuntu、Python 3.11 / 3.13 上运行这些检查；它不代替本机 Blender 渲染测试或模型视觉验收。
