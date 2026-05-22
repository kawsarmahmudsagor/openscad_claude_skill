# OpenSCAD Claude Skill — Setup & Usage Guide

> A Claude Code skill for AI-driven 3D CAD using OpenSCAD. This guide covers everything you need to install, configure, and run the skill from scratch.

---

## Table of Contents

1. [What This Project Is](#what-this-project-is)
2. [How It Works — Architecture Overview](#how-it-works--architecture-overview)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Verifying the Installation](#verifying-the-installation)
6. [Running the Project — Six Modes](#running-the-project--six-modes)
   - [Design Mode](#1-design-mode)
   - [Replicate Mode](#2-replicate-mode)
   - [Reconstruct Mode](#3-reconstruct-mode)
   - [Refine Mode](#4-refine-mode)
   - [Export Mode](#5-export-mode)
   - [Analyze Mode](#6-analyze-mode)
7. [Project Management](#project-management)
8. [Common Workflows](#common-workflows)
9. [Environment Variables](#environment-variables)
10. [Eval Framework](#eval-framework)
11. [Troubleshooting](#troubleshooting)

---

## What This Project Is

This is a **Claude Code skill** — a structured instruction set that teaches Claude Code how to perform a specific domain of work. In this case, the domain is **programmatic 3D CAD using OpenSCAD**.

When installed, Claude Code gains the ability to:
- Generate `.scad` files from natural-language descriptions
- Render multi-angle PNG previews and use AI vision to evaluate them
- Convert existing STL meshes back into clean, parametric OpenSCAD code
- Reproduce physical objects from reference photos
- Export production-ready STL and 3MF files for 3D printing
- Validate designs for printability

The skill is defined in `SKILL.md`, which Claude Code reads automatically when triggered. The shell scripts and Python tools in `scripts/` are the execution engine that Claude invokes during its workflow.

---

## How It Works — Architecture Overview

```
User Request (natural language)
        │
        ▼
  Claude Code reads SKILL.md
        │
        ▼
  Auto-detects mode (Design / Reconstruct / Export / etc.)
        │
        ├──► Runs shell scripts (openscad-render.sh, openscad-project.sh, etc.)
        │
        ├──► Runs Python tools (openscad-sdf-optimize.py, openscad-adaptive-slice.py, etc.)
        │
        ├──► Reads references/ (language-reference.md, reconstruction-guide.md)
        │
        ├──► Uses templates/ (enclosure.scad, bracket.scad, printable-lib.scad)
        │
        └──► Produces: .scad source, PNG previews, .stl / .3mf exports
```

The feedback loop for design is iterative:

```
Write .scad  →  Render PNG  →  AI vision review  →  Fix issues  →  Repeat
```

For STL reconstruction, the pipeline is:

```
Input STL  →  Mesh Analysis  →  SVG Profile Slicing  →  Primitive Detection  →  Write .scad  →  Mesh Comparison  →  Verify Accuracy %
```

---

## Prerequisites

Install all dependencies before using the skill.

### 1. OpenSCAD (required)

OpenSCAD must be installed and accessible on your `PATH`.

```bash
# macOS
brew install openscad

# Ubuntu / Debian
sudo apt-get install openscad

# Verify
openscad --version
```

### 2. Python 3 with libraries (required for STL reconstruction)

```bash
pip3 install trimesh numpy scipy rtree shapely
```

These libraries power:
- `trimesh` — mesh loading, volume analysis, watertight checking
- `numpy` — numerical geometry operations
- `scipy` — parameter optimization (SDF optimizer)
- `rtree` / `shapely` — 2D polygon operations for profile slicing

### 3. admesh (optional, for mesh validation)

```bash
# macOS
brew install admesh

# Ubuntu
sudo apt-get install admesh
```

### 4. Claude Code CLI (required)

Install the Claude Code CLI from Anthropic. This is the tool that reads SKILL.md and orchestrates the entire workflow.

---

## Installation

### Option 1: Clone and symlink (recommended for updates)

```bash
git clone https://github.com/andreahaku/openscad_claude_skill.git ~/Development/Claude/openscad_claude_skill

mkdir -p ~/.claude/skills
ln -sf ~/Development/Claude/openscad_claude_skill ~/.claude/skills/openscad
```

Symlinking means you can `git pull` to update the skill in place.

### Option 2: Clone directly into skills folder

```bash
git clone https://github.com/andreahaku/openscad_claude_skill.git ~/.claude/skills/openscad
```

After either option, your skills directory should look like:

```
~/.claude/skills/
└── openscad/
    ├── SKILL.md
    ├── scripts/
    ├── references/
    ├── templates/
    └── eval/
```

---

## Verifying the Installation

Run these checks in order:

```bash
# 1. Check OpenSCAD is on PATH
openscad --version

# 2. Check Python dependencies
python3 -c "import trimesh, numpy, scipy, shapely; print('All Python deps OK')"

# 3. Quick render test using a built-in template
bash ~/.claude/skills/openscad/scripts/openscad-render.sh quick \
     ~/.claude/skills/openscad/templates/bracket.scad

# Expected output: "Preview saved: .../previews/quick-preview.png"
```

If the render test produces a PNG file without errors, the skill is ready.

---

## Running the Project — Six Modes

The skill auto-detects which mode to use from your request. You can also trigger it explicitly in Claude Code using `/openscad`.

### 1. Design Mode

**Trigger keywords:** "design a", "create a", "make a", "build a"

Creates a new parametric 3D model from a text description. Claude will:
1. Initialize a project directory under `~/openscad-projects/`
2. Write a `.scad` file using the Feature Tree pattern (parameters → body → features → assembly)
3. Render a 4-angle preview
4. Visually evaluate the PNG and iterate if needed

**Example:**
```
/openscad design a parametric enclosure for a Raspberry Pi 4 with ventilation slots
```

**What Claude will do internally:**
```bash
bash ~/.claude/skills/openscad/scripts/openscad-project.sh init "rpi4-enclosure"
# ... writes src/main.scad ...
bash ~/.claude/skills/openscad/scripts/openscad-render.sh preview src/main.scad
```

---

### 2. Replicate Mode

**Trigger keywords:** "reproduce this from the photo", "match this object", "copy this"

Reproduces a physical object from reference images. Claude uses AI vision to measure proportions, identify features, and translate them into OpenSCAD parameters.

**Example:**
```
/openscad reproduce this from the photo [attach image]
```

---

### 3. Reconstruct Mode

**Trigger keywords:** "convert this STL", "reverse-engineer", "STL to SCAD"

Converts an existing binary STL mesh into clean, parametric OpenSCAD code. This is the most complex pipeline in the skill:

1. `openscad-stl-analyze.sh` or `openscad-stl-reconstruct.sh` — bounding box, triangle count, gap analysis
2. SVG profile slicing via `projection(cut=true)` at 5 Z levels
3. Primitive detection using RANSAC normal analysis
4. Claude writes the `.scad` code using the sculptor approach
5. `openscad-stl-compare.sh` — boolean diff rendering + accuracy percentage

**Example:**
```
/openscad convert /path/to/model.stl into parametric OpenSCAD code
```

**Expected accuracy:** 95–97% geometric match for simple to moderately complex meshes.

---

### 4. Refine Mode

**Trigger keywords:** "make it taller", "add fillets", "change the width", "update the design"

Iterates on an existing `.scad` file. Claude reads the current design, applies the requested changes, re-renders, and shows you the updated result.

**Example:**
```
/openscad make the enclosure 10mm taller and add a cable routing slot on the side
```

---

### 5. Export Mode

**Trigger keywords:** "export STL", "export 3MF", "ready for printing", "generate final"

Renders production export files using `openscad-render.sh export`. Supports parameter overrides via `-D` flags.

**Example:**
```
/openscad export the bracket with width=100 and wall=3
```

**What Claude will do internally:**
```bash
bash ~/.claude/skills/openscad/scripts/openscad-render.sh stl src/main.scad \
     -D 'width=100' -D 'wall=3'
```

---

### 6. Analyze Mode

**Trigger keywords:** "check printability", "analyze this design", "validate"

Runs `openscad-render.sh analyze` and `openscad-validate.sh` to check for:
- Wall thickness violations (min 1.2 mm)
- Overhangs exceeding 45°
- Manifold/watertight mesh issues
- Syntax errors and parameter warnings

**Example:**
```
/openscad check the printability of my current design
```

---

## Project Management

All projects are stored under `~/openscad-projects/`. Use `openscad-project.sh` to manage them:

```bash
# Create a new project
bash ~/.claude/skills/openscad/scripts/openscad-project.sh init "my-project"

# List all projects
bash ~/.claude/skills/openscad/scripts/openscad-project.sh list

# Show project info
bash ~/.claude/skills/openscad/scripts/openscad-project.sh info "my-project"

# Clean build artifacts (keeps source files)
bash ~/.claude/skills/openscad/scripts/openscad-project.sh clean "my-project"
```

Each project has this structure:

```
~/openscad-projects/my-project/
├── src/
│   └── main.scad       ← Your OpenSCAD source
├── output/
│   ├── main.stl        ← Exported STL
│   └── main.3mf        ← Exported 3MF
├── previews/
│   └── *.png           ← Rendered preview images
└── README.md
```

---

## Common Workflows

### Design from scratch and export

```bash
# 1. In Claude Code:
/openscad design a phone stand, 80mm wide, 50mm deep, 30° angle, cable slot at bottom

# Claude will:
# - Create ~/openscad-projects/phone-stand/
# - Write src/main.scad
# - Render and review preview PNGs
# - Iterate until design looks right

# 2. Then export:
/openscad export STL for the phone stand
```

### Reconstruct an STL

```bash
# In Claude Code:
/openscad convert ~/Downloads/part.stl into parametric OpenSCAD

# Claude will run the full pipeline and report accuracy like:
# "Geometric accuracy: 96.3% — Good match (minor differences)"
```

### Override parameters at export time

```bash
# In Claude Code:
/openscad export the enclosure with wall=3 and height=50

# Internally:
bash ~/.claude/skills/openscad/scripts/openscad-render.sh stl src/main.scad \
     -D 'wall=3' -D 'height=50'
```

### Quick render test (outside Claude Code)

```bash
bash ~/.claude/skills/openscad/scripts/openscad-render.sh quick my-model.scad
bash ~/.claude/skills/openscad/scripts/openscad-render.sh preview my-model.scad
bash ~/.claude/skills/openscad/scripts/openscad-render.sh export my-model.scad
```

---

## Environment Variables

You can customize behavior by setting these before running scripts or starting Claude Code:

| Variable | Default | Description |
|---|---|---|
| `OPENSCAD_BIN` | `$(which openscad)` | Full path to the OpenSCAD binary |
| `OPENSCAD_IMGSIZE` | `800,600` | Preview image resolution (width,height) |
| `OPENSCAD_COLORSCHEME` | `DeepOcean` | OpenSCAD color theme for renders |

**Example:**
```bash
export OPENSCAD_BIN=/usr/local/bin/openscad
export OPENSCAD_IMGSIZE=1920,1080
export OPENSCAD_COLORSCHEME=Cornfield
```

---

## Eval Framework

The skill ships with a built-in test suite in `eval/`. It defines 4 test scenarios with 20 binary assertions:

| Scenario | Assertions | What It Tests |
|---|---|---|
| `design-simple-box` | 6 | Project init, parametric .scad structure, rendering |
| `design-sculptor` | 5 | Sculptor approach, epsilon usage, printability |
| `reconstruct-stl` | 5 | Analysis-first pipeline, SVG profiling, mesh comparison |
| `export-parametric` | 4 | `-D` flag overrides, STL output path/size reporting |

**Baseline:** 20/20 assertions passing (100%).

To review test definitions:
```bash
cat ~/.claude/skills/openscad/eval/eval.json
```

To review historical results:
```bash
cat ~/.claude/skills/openscad/eval/results.jsonl
```

---

## Troubleshooting

### "openscad: command not found"

OpenSCAD is not on your `PATH`. Either install it or set `OPENSCAD_BIN`:

```bash
export OPENSCAD_BIN=/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD  # macOS .app
```

### "ERROR: File not found" when rendering

The `.scad` file path you passed to the render script doesn't exist. Always use an absolute or relative path that resolves from your current working directory.

### "Category: HEADLESS_PREVIEW" from validate script

PNG preview rendering requires an OpenGL context. On headless servers (CI, Docker), PNG export may fail while STL export still works. Use `--render` mode and check the STL output directly.

### Python errors during STL reconstruction

Install all Python dependencies:
```bash
pip3 install trimesh numpy scipy rtree shapely --upgrade
```

### "Top level object is empty" in OpenSCAD

This means your `.scad` code produces no geometry. Common causes:
- A `difference()` subtracted more than was there
- Parameters set to zero or negative values
- Module defined but not called at the top level

Run validate to get categorized feedback:
```bash
bash ~/.claude/skills/openscad/scripts/openscad-validate.sh src/main.scad
```

### Mesh comparison shows low accuracy

Re-run reconstruction with more Z-level slices, or use `openscad-sdf-optimize.py` to fine-tune numerical parameters automatically:

```bash
python3 ~/.claude/skills/openscad/scripts/openscad-sdf-optimize.py model.stl stadium-slot --verbose
```
