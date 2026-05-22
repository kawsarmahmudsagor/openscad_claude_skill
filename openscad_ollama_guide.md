# Porting Claude Code Skills to Ollama: A Research & Implementation Guide

**Subject:** LLM Skills — Architecture, Implementation, and Runtime Portability  
**Model:** `qwen3-vl:235b-cloud` via Ollama  
**Case Study:** OpenSCAD Skill by [@andreahaku](https://github.com/andreahaku/openscad_claude_skill)

---

## Table of Contents

1. [What Are LLM Skills?](#1-what-are-llm-skills)
2. [How Skills Are Implemented](#2-how-skills-are-implemented)
3. [Skill Routing](#3-skill-routing)
4. [The OpenSCAD Skill — Full Architecture Analysis](#4-the-openscad-skill--full-architecture-analysis)
5. [Claude Code as an Agentic Runtime](#5-claude-code-as-an-agentic-runtime)
6. [Porting to Ollama — What Changes and What Doesn't](#6-porting-to-ollama--what-changes-and-what-doesnt)
7. [The Tool Gap — What Must Be Built](#7-the-tool-gap--what-must-be-built)
8. [The Agentic Harness — Implementation](#8-the-agentic-harness--implementation)
9. [Vision Feedback Loop](#9-vision-feedback-loop)
10. [Setup & Usage Instructions](#10-setup--usage-instructions)
11. [Tradeoffs & Limitations](#11-tradeoffs--limitations)

---

## 1. What Are LLM Skills?

"Skills" in LLM systems is an overloaded term. There are two fundamentally different concepts that share the name:

### Type A — Tool-Use / Function-Calling Skills

The ability of an LLM to call external tools (web search, code execution, APIs). These are defined as JSON schemas passed to the model at inference time.

```json
{
  "name": "web_search",
  "description": "Search the web for current information",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": { "type": "string" }
    },
    "required": ["query"]
  }
}
```

The LLM reads the `description` to decide *when* to call the tool and the `input_schema` to know *what arguments* to pass. Execution happens outside the model — in your application code — and results are fed back into the conversation.

**How the loop works:**
```
User prompt
    → LLM decides to call a tool
    → Returns a tool_use block (name + args)
    → Your code runs the actual tool
    → Result is injected as tool_result
    → LLM continues its response
```

### Type B — Prompt-Pack Skills

Structured instruction documents injected into the LLM's context before it answers. They encode task-specific best practices — procedures, constraints, patterns, and pitfalls that the model should follow. This is the type used by Claude Code.

A prompt-pack skill is just **text**. Any LLM that can read a document can follow one.

---

## 2. How Skills Are Implemented

### Prompt-Pack Structure

A well-designed prompt-pack skill has the following layers:

| Layer | Purpose | Example |
|---|---|---|
| **Frontmatter / Metadata** | Routing signal, tool permissions, version | YAML header in SKILL.md |
| **Environment section** | Paths, binaries, OS constraints | Binary location, working dirs |
| **Mode detection** | How to classify the user's intent | 6 modes in OpenSCAD skill |
| **Per-mode workflows** | Step-by-step instructions | Design → Preview → Iterate |
| **Hard rules** | Non-negotiable constraints, known failure patterns | "Sculptor approach: mandatory" |
| **Code patterns** | Copy-paste templates and snippets | Rounded box module, shell module |
| **Error handling** | What to do when things go wrong | Parse errors, geometry errors |
| **Reference pointers** | "Read this file if unsure about X" | language-reference.md |

### Retrieval-Augmented Prompting

Skills are a form of **retrieval-augmented prompting**: instead of hoping the LLM learned how to do something from training, you fetch precise, up-to-date instructions at inference time and inject them into the context. This is:

- More reliable than fine-tuning for task-specific behavior
- More auditable — you can read exactly what the model was told
- Easier to update — change a text file, not model weights
- Cheaper — no retraining cost

### The On-Demand Reference Pattern

Large skills don't dump all their knowledge into context at once. Instead, they use an on-demand reading pattern:

```
SKILL.md → "Read references/language-reference.md if unsure about syntax"
                    ↓
         LLM only reads it when needed
                    ↓
         Context window stays lean
```

---

## 3. Skill Routing

Skill routing is the mechanism that decides *which skill* to load for a given user request.

### Routing Approaches

**Rule-Based Routing** — Keyword or regex matching. Fast and predictable, but brittle against paraphrasing.

**LLM-as-Router** — A small, cheap model reads the request and skill descriptions, outputs which skill(s) to activate. Handles natural language well but adds latency.

**Semantic / Embedding Routing** — Skills are stored in a vector database. At query time, the request is embedded and compared via cosine similarity. Scales to thousands of skills.

```
Query embedding → Vector DB search → top-k skill matches → load those skills
```

**Hierarchical Routing** — Skills are organized in a tree. The router narrows down level by level (category → specific skill), faster than scanning all leaves.

**Self-Routing** — The same LLM reads a list of skill names + descriptions in its system prompt and decides which files to load. Zero infrastructure, but requires a good system prompt.

### Routing Decision Matrix

| Method | Scalability | Accuracy | Latency | Infrastructure |
|---|---|---|---|---|
| Rule-based | Low | Low | ~0ms | None |
| LLM-as-router | Medium | High | +500ms | Second LLM call |
| Semantic/Embedding | High | High | ~10ms | Vector DB |
| Hierarchical | High | Medium | ~5ms | Taxonomy design |
| Self-routing | Medium | High | ~0ms | Good system prompt |

---

## 4. The OpenSCAD Skill — Full Architecture Analysis

Repository: `https://github.com/andreahaku/openscad_claude_skill`

### File Structure

```
openscad_claude_skill/
├── SKILL.md                    ← The brain: 917 lines of routing metadata + workflows
├── scripts/
│   ├── openscad-render.sh      ← Core render/export/preview engine (7 commands)
│   ├── openscad-project.sh     ← Project scaffolding (init/list/clean)
│   ├── openscad-validate.sh    ← Strict error categorization
│   ├── openscad-stl-analyze.sh ← Mesh analysis (bbox, cross-sections, gap detection)
│   ├── openscad-stl-reconstruct.sh  ← SVG profiling pipeline
│   ├── openscad-stl-compare.sh ← Boolean diff, geometric accuracy %
│   ├── openscad-sdf-optimize.py     ← SDF parameter optimizer (IoU scoring)
│   ├── openscad-adaptive-slice.py   ← Multi-axis feature mapping
│   └── openscad-auto-reconstruct.py ← Auto .scad generation
├── references/
│   ├── language-reference.md   ← Complete OpenSCAD v2021.01 cheat sheet
│   └── reconstruction-guide.md ← Lessons from real reconstructions
├── templates/
│   ├── enclosure.scad          ← Parametric electronics box with lid
│   ├── bracket.scad            ← L-bracket with countersunk holes
│   └── printable-lib.scad      ← Reusable 3D printing modules
└── eval/
    ├── eval.json               ← 4 test scenarios, 20 binary assertions
    └── results.jsonl           ← Test results log
```

### SKILL.md Frontmatter (The Routing Manifest)

```yaml
---
name: openscad
description: >
  Programmatic 3D CAD with OpenSCAD. Triggers on: 3D model, STL,
  3D print, parametric design, openscad, CAD, enclosure, bracket.
argument-hint: "<description of object or path to .scad file>"
allowed-tools: "Bash(*),Read,Edit,Write,Glob,Grep,Agent"
---
```

This header tells Claude Code's skill router: when to trigger, what the user passes in, and which built-in tools the LLM is allowed to use during this skill's execution.

### The 6 Operating Modes

The skill auto-detects which mode to enter based on user intent:

| Mode | Trigger phrases | What it does |
|---|---|---|
| **Design** | "design a bracket", "make a box" | Create new 3D models from descriptions |
| **Replicate** | "reproduce this from the photo" | Reverse-engineer objects from reference images |
| **Reconstruct** | "convert this STL to SCAD" | Reverse-engineer STL meshes into parametric code |
| **Refine** | "make it taller", "add fillets" | Iterate on existing designs |
| **Export** | "export STL", "ready for printing" | Generate production files |
| **Analyze** | "check printability" | Validate designs for 3D printing |

### The 3-Tier Tool System

```
┌─────────────────────────────────────────────────────────────┐
│  TIER 1 — Shell Scripts (Bash)                              │
│  openscad-render.sh      → renders PNG previews, STL, 3MF  │
│  openscad-project.sh     → scaffolds project directories   │
│  openscad-stl-compare.sh → diffs two STL meshes visually   │
│  openscad-stl-reconstruct.sh → full SVG profiling pipeline │
├─────────────────────────────────────────────────────────────┤
│  TIER 2 — Python Scripts (advanced analysis)               │
│  openscad-profile-extract.py  → extrusion axis detection   │
│  openscad-adaptive-slice.py   → multi-axis feature mapping │
│  openscad-sdf-optimize.py     → IoU scoring optimizer      │
│  openscad-auto-reconstruct.py → parametric .scad generator │
├─────────────────────────────────────────────────────────────┤
│  TIER 3 — OpenSCAD binary (called by Tier 1 scripts)       │
│  openscad --export-format png → preview images             │
│  openscad --export-format stl → printable mesh export      │
└─────────────────────────────────────────────────────────────┘
```

### The Vision Feedback Loop

The most architecturally interesting feature — the LLM uses its vision capability to close a design-quality loop without user involvement:

```
Write .scad code
    ↓
bash openscad-render.sh preview → 4 PNG images (front, side, top, isometric)
    ↓
LLM reads each PNG (vision)
    ↓
"Does this match the user's request? Are proportions correct? Will it print?"
    ↓  if no ↙
Edit .scad → re-render → re-read PNGs → repeat
    ↓  if yes ↘
Show result to user
```

### The Eval Framework

`eval/eval.json` defines 20 binary assertions across 4 test scenarios. Each assertion is a yes/no behavioral question — not about output content, but about *whether the model followed the correct process*:

- "Did it run analysis scripts *before* writing any code?"
- "Did it use the sculptor approach?"
- "Did it read the rendered PNG to self-verify?"
- "Did it run mesh comparison to report geometric accuracy?"

This allows automated regression testing when the skill is updated.

---

## 5. Claude Code as an Agentic Runtime

This is the most important architectural insight: **Claude Code is not just the LLM**. It is the entire agentic runtime.

```
Claude Code = LLM + Tool Executor + Skill Router + File System + Agentic Loop
```

When `SKILL.md` declares `allowed-tools: "Bash(*), Read, Edit, Write, Glob, Grep"`, those are **Claude Code's built-in agent capabilities** — not bash or grep the programs. They are the *bridge* between the LLM's text output and your actual filesystem and OS.

This distinction is crucial for porting: Ollama gives you just the LLM. None of the runtime comes with it.

---

## 6. Porting to Ollama — What Changes and What Doesn't

### Portability Assessment

| Component | Portable? | Action Required |
|---|---|---|
| All `.sh` shell scripts | ✅ 100% | Keep exactly as-is |
| All `.py` Python scripts | ✅ 100% | Keep exactly as-is |
| `.scad` templates | ✅ 100% | Keep exactly as-is |
| `references/*.md` | ✅ 100% | Keep exactly as-is |
| SKILL.md *content* (workflows, rules) | ✅ as system prompt | Strip YAML frontmatter |
| SKILL.md YAML frontmatter | ❌ | Claude Code-specific; ignored |
| `Bash(*)` tool | ❌ rebuild | Implement via `subprocess` |
| `Read` tool (vision + text) | ❌ rebuild | base64 for images, `open()` for text |
| `Write / Edit / Glob / Grep` tools | ❌ rebuild | Simple Python file operations |
| Agentic loop | ❌ rebuild | The harness itself |
| Skill auto-routing | ❌ rebuild | Manual or embedding-based |

### The Model: `qwen3-vl:235b-cloud`

**Vision (`vl` suffix)** — Qwen3-VL is a Vision-Language model. This means the PNG-reading feedback loop works natively via base64 image injection into the message array.

**Tool calling** — Qwen3 models support Ollama's tool-calling format (OpenAI-compatible JSON schema). The agentic loop with tool calls is feasible.

**`-cloud` suffix** — Verify this tag exists in your Ollama setup:

```bash
ollama show qwen3-vl:235b-cloud
# or
ollama list
# if not present:
ollama pull qwen3-vl:235b-cloud
```

---

## 7. The Tool Gap — What Must Be Built

### The Key Distinction

There are two completely different things called "tools" in this context:

**The skill's tools** (shell scripts in `scripts/`) — These are files on disk. They don't care who calls them. Zero work needed.

**The LLM's tools** (Claude Code built-ins) — These are the bridge between the LLM's intentions and your OS. With Ollama, you build this bridge yourself.

### What Each Built-In Does

| Claude Code Built-in | What it does | Python implementation |
|---|---|---|
| `Bash(command)` | Runs any shell command | `subprocess.run(command, shell=True)` |
| `Read(path)` for text | Reads a text file | `open(path).read()` |
| `Read(path)` for images | Reads image into vision context | `base64.b64encode(open(path,'rb').read())` |
| `Write(path, content)` | Creates/overwrites a file | `open(path,'w').write(content)` |
| `Edit(path, old, new)` | Replaces unique string in file | `content.replace(old_str, new_str, 1)` |
| `Glob(pattern)` | Lists files by pattern | `glob.glob(pattern, recursive=True)` |
| `Grep(pattern, path)` | Searches in files | `subprocess.run(['grep', '-n', pattern, path])` |

Each of these is a tiny Python function — 5 to 15 lines each. The complexity is in the harness that orchestrates them, not in the individual tools.

---

## 8. The Agentic Harness — Implementation

### Full Architecture

```
openscad_ollama_harness.py
│
├── load_skill()             → Read SKILL.md, strip frontmatter, patch paths
│
├── TOOLS (list)             → JSON schema definitions sent to Ollama
│
├── Tool implementations
│   ├── tool_bash()          → subprocess.run with safety checks + timeout
│   ├── tool_read_file()     → open() for text files
│   ├── tool_read_image()    → base64 encode for PNG/JPEG
│   ├── tool_write_file()    → open('w') with mkdir -p
│   ├── tool_edit_file()     → uniqueness-checked string replacement
│   ├── tool_glob()          → glob.glob with recursive support
│   └── tool_grep()          → subprocess grep with line numbers
│
├── dispatch_tool()          → Routes tool name → implementation
│
└── run_agent()              → Main agentic loop
    ├── Outer loop: conversation turns (one per user message)
    └── Inner loop: tool call iterations (up to max_iterations)
        ├── Inject pending images as multimodal messages
        ├── Call ollama.chat() with tools
        ├── If no tool_calls → print final answer, break
        └── For each tool_call → dispatch → append result → continue
```

### The Message Flow

```
[system]  SKILL.md content
[user]    "Design a 60x40x30mm box"
     ↓
Ollama → tool_call: bash("openscad-project.sh init box")
[tool]    "Project created at ~/openscad-projects/box/"
     ↓
Ollama → tool_call: write_file("~/openscad-projects/box/src/main.scad", "...")
[tool]    "Written 847 chars to main.scad"
     ↓
Ollama → tool_call: bash("openscad-render.sh preview main.scad")
[tool]    "Rendered: previews/iso.png, previews/front.png ..."
     ↓
Ollama → tool_call: read_image("previews/iso.png")
[tool]    "[Image loaded: previews/iso.png]"
[user]    [multimodal: base64 image + "Analyze this preview"]
     ↓
Ollama → (no tool call) → "The box looks correct. Here is your design..."
```

### Safety Mechanisms

The harness includes several safety constraints:

**Command blocklist** — A set of known-dangerous patterns (`rm -rf /`, `mkfs`, fork bomb) that are checked before executing any bash command.

**Output truncation** — All tool outputs are capped at 8,000 characters to prevent context window overflow from runaway commands.

**Execution timeout** — Bash commands time out after 300 seconds to prevent hangs during complex renders.

**Edit uniqueness check** — `edit_file` verifies that `old_str` appears exactly once before applying the edit, preventing incorrect multi-site replacements.

**Iteration cap** — The inner agentic loop stops after `max_iterations` (default: 50) to prevent infinite loops.

---

## 9. Vision Feedback Loop

This is the most architecturally sensitive part of the port.

### How Claude Code Does It

Claude Code's `Read` tool natively handles image files. When the model calls `Read("preview.png")`, Claude Code:
1. Detects it's an image by extension
2. Encodes it as base64
3. Injects it as a vision block into the next model call

### How the Harness Does It

The harness replicates this with a two-step mechanism:

**Step 1** — When `read_image` is called, the tool executor returns a text acknowledgment to the current message and stores the image payload in `pending_images`.

**Step 2** — At the top of the next iteration of the agentic loop, if `pending_images` is non-empty, a multimodal user message is prepended before the Ollama call:

```python
# Multimodal message format for Ollama vision models
{
    "role": "user",
    "content": [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{b64_data}"
            }
        },
        {
            "type": "text",
            "text": "The above image(s) are the rendered preview(s). Analyze them."
        }
    ]
}
```

This correctly injects the image into the LLM's vision context before it forms its next response.

---

## 10. Setup & Usage Instructions

### Prerequisites

```bash
# 1. Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# 2. Pull the model
ollama pull qwen3-vl:235b-cloud

# 3. Install OpenSCAD
brew install openscad          # macOS
sudo apt install openscad      # Ubuntu/Debian

# 4. Install Python dependencies for OpenSCAD skill scripts
pip3 install trimesh numpy scipy rtree shapely

# 5. Install mesh validation tool
brew install admesh            # macOS
sudo apt install admesh        # Ubuntu/Debian

# 6. Install Python Ollama client
pip3 install ollama
```

### Install the OpenSCAD Skill

```bash
# Clone the skill
git clone https://github.com/andreahaku/openscad_claude_skill.git ~/openscad_skill

# Symlink to the expected location
mkdir -p ~/.claude/skills
ln -sf ~/openscad_skill ~/.claude/skills/openscad

# Verify OpenSCAD is working
openscad --version
bash ~/.claude/skills/openscad/scripts/openscad-render.sh quick \
     ~/.claude/skills/openscad/templates/bracket.scad
```

### Install the Harness

```bash
# Save openscad_ollama_harness.py to your preferred location
cp openscad_ollama_harness.py ~/openscad_ollama_harness.py
chmod +x ~/openscad_ollama_harness.py
```

### Run the Harness

```bash
# Default — uses qwen3-vl:235b-cloud and ~/.claude/skills/openscad
python3 openscad_ollama_harness.py

# Custom model
python3 openscad_ollama_harness.py --model qwen3-vl:235b-cloud

# Custom skill path
python3 openscad_ollama_harness.py --skill-path ~/my-skills/openscad

# All options
python3 openscad_ollama_harness.py \
    --model gemma4:31b \
    --skill-path ~/.claude/skills/openscad \
    --max-iterations 50
```

EXAMPLE:
You: Replicate this object from the photo /home/xr23/Projects/openscad_claude_skill/images/ring_1.png. 
Write the OpenSCAD code, render it, analyse the preview against the reference, fix any 
discrepancies, and repeat until the result closely matches the photo. 
Do not stop between iterations — keep going until you are satisfied with the result.

### Example Session

```
═══════════════════════════════════════════════════════════
  OpenSCAD Skill Harness  —  Ollama / Qwen3-VL
  Model      : qwen3-vl:235b-cloud
  Skill path : /home/user/.claude/skills/openscad
═══════════════════════════════════════════════════════════
  Type your request. 'quit' or Ctrl-C to exit.

You: Design a parametric enclosure for a Raspberry Pi 4, 90x65x30mm, 2mm walls

  [→ Ollama, iteration 1]
  🔧 bash: openscad-project.sh init rpi4-enclosure
  ✏️  write_file: ~/openscad-projects/rpi4-enclosure/src/main.scad
  🔧 bash: openscad-render.sh preview ~/openscad-projects/rpi4-enclosure/src/main.scad
  🖼️  read_image: ~/openscad-projects/rpi4-enclosure/previews/iso.png
  [→ Ollama, iteration 5]
Assistant: Your design is ready. The enclosure is 90x65x30mm with 2mm walls.
The preview shows correct proportions. STL exported to:
~/openscad-projects/rpi4-enclosure/output/model.stl
```

---

## 11. Tradeoffs and Limitations

### Comparison: Claude Code + Skill vs Ollama Harness

| Dimension | Claude Code + Skill | Ollama + Harness |
|---|---|---|
| Setup effort | Install Claude Code, symlink skill | Build harness (~350 lines) + install deps |
| Skill routing | Automatic from YAML frontmatter | Manual trigger or embedding-based |
| Tool execution | Built-in, battle-tested | Custom implementation |
| Vision loop | Native `Read` tool | Manual base64 injection via pending queue |
| Cost | Claude API pricing per token | Local inference (free after setup) |
| Privacy | Data sent to Anthropic servers | Fully local if using local Ollama model |
| Portability | Locked to Claude Code CLI | Runs anywhere Python runs |
| Model quality | Claude Sonnet/Opus | Qwen3-VL 235B (very capable) |
| Tool reliability | Extremely reliable | Depends on model's tool-calling consistency |

### Known Risks with the Ollama Approach

**Tool call format inconsistency** — Some Ollama model versions return tool call arguments as a JSON string rather than a parsed dict. The harness handles this by attempting JSON parsing when a string is received.

**Context window pressure** — At 235B parameters, Qwen3-VL has strong reasoning but the full SKILL.md is 917 lines. With long agentic loops, the context window (set to 32,768 tokens) can fill up. Mitigation: summarize or truncate older tool results.

**Vision multimodal timing** — The harness queues images and injects them at the start of the next iteration. If the model calls `read_image` and then immediately asks another tool question in the same response, the image analysis happens one step later than ideal.

**Render latency** — OpenSCAD rendering (especially at `$fn=128`) can take 30-120 seconds. The harness has a 300-second timeout which is sufficient, but users should expect waits.

### Extending the Harness

**Adding a new tool** — Three steps:
1. Add a function `tool_mytool(args) -> str`
2. Add a JSON schema entry to the `TOOLS` list
3. Add a case in `dispatch_tool()`

**Adding skill routing** — Replace the hardcoded `load_skill()` call with an embedding-based router that scores all available SKILL.md files against the user's first message.

**Multi-skill support** — Load multiple SKILL.md files from a skills directory, route to the best one, or load several and concatenate them if the request spans domains.

**Conversation memory** — The `messages` list grows unboundedly in the current implementation. For long sessions, add a summarization step: periodically compress older messages into a summary block while preserving the system prompt and recent context.

---

## Appendix: Harness Quick Reference

### CLI Options

```
--model            Ollama model tag          (default: qwen3-vl:235b-cloud)
--skill-path       Path to skill directory   (default: ~/.claude/skills/openscad)
--max-iterations   Max tool calls per turn   (default: 50)
```

### Available Tools (sent to Ollama)

| Tool | Key args | Purpose |
|---|---|---|
| `bash` | `command`, `working_dir?` | Run any shell command |
| `read_file` | `path` | Read text file |
| `read_image` | `path` | Read PNG/JPEG for vision analysis |
| `write_file` | `path`, `content` | Create or overwrite a file |
| `edit_file` | `path`, `old_str`, `new_str` | Replace unique string in file |
| `glob` | `pattern` | List files by glob pattern |
| `grep` | `pattern`, `path`, `recursive?` | Search files for a pattern |

### Environment Variables (OpenSCAD scripts)

| Variable | Default | Purpose |
|---|---|---|
| `OPENSCAD_BIN` | `$(command -v openscad)` | Path to OpenSCAD binary |
| `OPENSCAD_IMGSIZE` | `800,600` | Preview image dimensions |
| `OPENSCAD_COLORSCHEME` | `DeepOcean` | Render color scheme |

### Render Script Commands

```bash
openscad-render.sh quick <file.scad>           # Single isometric preview
openscad-render.sh preview <file.scad>         # 4-view (iso, front, right, top)
openscad-render.sh stl <file.scad> [-D ...]    # Export STL with optional param overrides
openscad-render.sh export <file.scad>          # Full export (STL + 3MF + PNG)
openscad-render.sh analyze <file.scad>         # Printability analysis
```
