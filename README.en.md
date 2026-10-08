[简体中文](README.md) | **English**

# Photo to Blender · Reference reconstruction workflow

Use product photos or design screenshots as references to create editable 3D models in local Blender, comparing and revising them through successive rounds.

This is a **Codex skill and local helper toolkit**. AI interprets images, writes object-specific modeling logic, and judges visual differences. The tools handle project initialization, Blender connections, staged renders, project checks, and delivery packaging.

## Capabilities

- Start modeling from product photos, concept drawings, or multi-view screenshots.
- Revise an existing `.blend` project against new references while preserving the source version.
- Check blockout silhouettes and connections before refining materials, colors, and lighting.
- Arrange independent structure and material reviews where subagents are supported.
- Deliver an editable project, required views, reference-comparison pages, review records, and a ZIP archive.

Best suited to products with clear silhouettes, components, and connections, such as headphones, speakers, small devices, and enclosure designs. Geometry must be built for each new reference; the workflow does not automatically turn any photo into a complete model.

## Requirements

| Component | Requirement or notes |
| --- | --- |
| Codex | An environment that supports local skills, tool execution, and image reading |
| Python | 3.11 or later; regular Python helpers use only the standard library |
| Blender | Verified on Windows + Blender 5.2; other versions and operating systems require verification |
| Blender Python | `blender_workflow.py` must run inside Blender using its bundled `bpy` |
| Blender MCP | Prefer an existing configured connection; the built-in client can also connect to a locally running service |
| GPU | Optional; use devices recognized by Blender or use CPU |

This repository neither bundles nor automatically installs the Blender MCP add-on. If needed, follow upstream [Blender MCP](https://github.com/ahujasid/blender-mcp) installation instructions.

## Installation

```sh
git clone https://github.com/gkgy/photo-to-blender.git
cd photo-to-blender
python -X utf8 install_skill.py
```

By default, the installer copies `skill/` to `$CODEX_HOME/skills/photo-to-blender`. If `CODEX_HOME` is unset, it uses `.codex/skills/photo-to-blender` under your home directory. Invoke the skill from a Codex task that can load it. If it does not appear in the current task, reopen the task or restart Codex.

Reinstalling the same version verifies and preserves existing files; a different version is not overwritten automatically. To update, move the original skill directory to a backup location before running the installer. Use `--destination` for a custom location; it points to the final skill directory.

## Getting started

Attach a photo to a Codex conversation and send:

```text
Use $photo-to-blender to create an editable Blender model from this photo.
Match the reference camera and blockout first, then check structure, materials, and colors.
Provide an overall render, front view, and side view; preserve the reference and export the project and comparison page.
```

For multiple images, provide the main reference, detail views, and dimension annotations together. You can also continue an existing model:

```text
Use $photo-to-blender to revise this Blender project against the new photo.
Focus on enclosure thickness and connection positions. Preserve the original,
save a new version, and show a before-and-after comparison.
```

You do not need to fill out every configuration field or run the helpers yourself; the skill guides the agent through these steps. For manual use, connection troubleshooting, or integrating your own scripts, read the [detailed usage guide (Chinese)](docs/usage.md).

## Process

1. **Reference analysis:** Record silhouettes, proportions, visible structures, materials, and occlusion. Distinguish annotated dimensions from estimates.
2. **Camera and blockout:** Build major components and check shapes from a camera close to the reference.
3. **Structure review:** Prioritize the largest silhouette, thickness, opening, and joint errors.
4. **Materials and lighting:** Handle fabric, cushions, plastic, metal, and transparent parts separately; render comparisons when useful.
5. **Review and delivery:** Save a new version, reopen it in an independent Blender process for checks, and generate illustrations and the delivery package.

When subagents are available, reviewers independently inspect the reference and stage outputs. Otherwise, the main agent reviews in separate rounds and records that accurately. Programmatic checks and visual reviews are recorded separately; passing file checks is not evidence of visual similarity.

## Outputs

Each project uses its own directory:

```text
product-replica/
├── project.json        # Scene, model version, render list
├── brief.json          # Reference analysis and assumptions
├── reference/          # Original reference-image bytes
├── model/              # Blender project and project scripts
├── renders/            # Blockouts, overall renders, required views
├── reviews/            # Review records and remaining differences
├── logs/               # Execution and project-check records
└── delivery/           # Versioned offline comparison pages and ZIP files
```

Views depend on the task. Exploded, section, and detail views can be added to the render list. Each packaging run creates a new directory; `delivery/latest.json` records the latest delivery location.

## Limitations

- One photo usually cannot establish real dimensions, rear surfaces, or internal structures. Unsupported details are labeled as assumptions.
- Visual similarity and exact engineering dimensions are different acceptance goals. Strict 1:1 reconstruction is not promised without measurements.
- Camera, geometry, materials, and color management can all cause differences and must be compared in stages.
- Each new object still needs modeling and review. Fixed completion times and compatibility with every Blender version are not guaranteed.

## Repository contents

| Path | Purpose |
| --- | --- |
| [`skill/SKILL.md`](skill/SKILL.md) | Agent workflow and delivery requirements |
| [`skill/scripts/project_workflow.py`](skill/scripts/project_workflow.py) | Initialization, environment diagnostics, local MCP client |
| [`skill/scripts/blender_workflow.py`](skill/scripts/blender_workflow.py) | Scene preparation, rendering, read-only project checks |
| [`skill/scripts/deliver_workflow.py`](skill/scripts/deliver_workflow.py) | Comparison pages, file manifest, ZIP |
| [`docs/usage.md`](docs/usage.md) | Manual use and troubleshooting (Chinese) |
| [`skill/references/review.md`](skill/references/review.md) | Independent-review methods and record format |

## Checks

```sh
python -X utf8 -m unittest discover -s tests -v
```

Tests cover project initialization, source preservation, installation safeguards, and uncertain MCP outcomes. They do not require Blender or network services. GitHub Actions runs these checks on Windows / Ubuntu with Python 3.11 / 3.13; this does not replace local Blender rendering tests or visual acceptance of a model.
