#!/usr/bin/env python3
"""
OpenSCAD Skill Harness for Ollama (qwen3-vl:235b-cloud)
========================================================
Replaces Claude Code as the agentic runtime for the OpenSCAD skill.
Keeps all shell scripts, Python tools, and templates untouched.

Architecture:
  Qwen3-VL <-> Tool Executor <-> OpenSCAD skill scripts / filesystem / OS

Usage:
  python3 openscad_ollama_harness.py
  python3 openscad_ollama_harness.py --skill-path ~/.claude/skills/openscad
  python3 openscad_ollama_harness.py --model qwen3-vl:235b-cloud

Fixes applied:
  2025-05 (a): Corrected multimodal image injection — images go inside the
               message dict as images=, not as OpenAI-style content list dicts.
  2025-05 (b): Fix 1 — auto-inject render PNG after every openscad-render.sh
               call so the model sees the output without calling read_image
               itself, preventing blind retry loops.
  2025-05 (c): Fix 3 — detect repeated write_file calls to the same .scad path
               without an intervening render. After WRITE_WARN_THRESHOLD writes,
               append a warning to the tool result. After WRITE_ESCALATE_THRESHOLD
               writes, inject a user-role message forcing a render call. This
               closes the blind rewrite loop where the model rewrites main.scad
               indefinitely because it has no visual feedback.
  2026-05 (d): Fix 4 — render-timestamp snapshot. The previews directory
               accumulates timestamped PNGs across iterations. The previous
               selector used isometric[0] which is alphabetical order, so it
               kept picking the OLDEST file in the directory. Now we snapshot
               time.time() immediately before invoking openscad-render.sh and
               only consider PNGs modified at or after that snapshot. Among
               those, isometric is preferred; otherwise newest wins.
"""

import argparse
import base64
import glob as glob_module
import json
import os
import subprocess
import textwrap
import time
from pathlib import Path

import ollama  # pip install ollama


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

DEFAULT_MODEL      = "gemma4:31b"
# DEFAULT_MODEL      = "qwen3-vl:235b-cloud"
DEFAULT_SKILL_PATH = Path.home() / ".claude" / "skills" / "openscad"
SKILL_FILE         = "SKILL.md"

# Write-loop guard (Fix 3)
# After this many writes to the same file without a render, append a warning
WRITE_WARN_THRESHOLD = 1
# After this many writes, escalate to a forceful user-role message
WRITE_ESCALATE_THRESHOLD = 1

# Render-snapshot guard (Fix 4)
# Slack in seconds applied to the render snapshot timestamp so we don't miss
# files whose mtime is a hair earlier than time.time() due to FS resolution
# or clock skew. Generous enough to cover any reasonable jitter, tight enough
# to never let in a previous-iteration file.
RENDER_MTIME_SLACK = 2.0

# Safety: commands that will never be executed
BLOCKED_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",   # fork bomb
]

# Max chars returned from a single tool call (prevents context overflow)
MAX_TOOL_OUTPUT = 8_000


# ─────────────────────────────────────────────
# TOOL DEFINITIONS  (sent to Ollama as schema)
# ─────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Execute a shell command and return its stdout + stderr. "
                "Use this to run openscad-render.sh, openscad-project.sh, "
                "python scripts, or any other OS command."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The full shell command to run."
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Optional working directory for the command."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the text content of a file. "
                "Use for .scad files, .md references, .json outputs, logs, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the text file."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_image",
            "description": (
                "Read a PNG or JPEG image file and return it for visual analysis. "
                "Use after openscad-render.sh generates preview images "
                "to check if the 3D model looks correct."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the image file."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file with given content. "
                "Use to write .scad source files, config files, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path of the file to write."
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write into the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace a unique string in a file with a new string. "
                "old_str must appear EXACTLY ONCE in the file. "
                "Use for targeted edits to existing .scad files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit."
                    },
                    "old_str": {
                        "type": "string",
                        "description": "The exact string to find and replace."
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The string to replace it with."
                    }
                },
                "required": ["path", "old_str", "new_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "List files matching a glob pattern. "
                "Use to discover project files, list previews, check what exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. ~/openscad-projects/**/*.scad"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search for a pattern in files. "
                "Use to find specific variable names, module definitions, or errors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The regex or string to search for."
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in."
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Search recursively in directories. Default true."
                    }
                },
                "required": ["pattern", "path"]
            }
        }
    }
]


# ─────────────────────────────────────────────
# TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────

def tool_bash(command: str, working_dir: str = None) -> str:
    """Run a shell command safely, return stdout+stderr."""

    # Safety check
    for blocked in BLOCKED_COMMANDS:
        if blocked in command:
            return f"[BLOCKED] Command contains forbidden pattern: '{blocked}'"

    # Headless fix: OpenSCAD preview needs OpenGL / a display.
    # On servers or WSL without an X server, wrap render calls with xvfb-run
    # (virtual framebuffer). No-op if already wrapped.
    if "openscad-render.sh" in command and "xvfb-run" not in command:
        command = f"xvfb-run -a {command}"

    print(f"\n  🔧 bash: {command[:120]}{'...' if len(command) > 120 else ''}")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=working_dir or os.getcwd(),
            timeout=300  # 5-minute timeout for long renders
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"

        # Truncate very long outputs
        if len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + f"\n... [truncated, {len(output)} total chars]"

        return output or "[command produced no output]"

    except subprocess.TimeoutExpired:
        return "[ERROR] Command timed out after 300 seconds"
    except Exception as e:
        return f"[ERROR] {e}"


def tool_read_file(path: str) -> str:
    """Read a text file."""
    path = os.path.expanduser(path)
    print(f"\n  📄 read_file: {path}")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > MAX_TOOL_OUTPUT:
            content = content[:MAX_TOOL_OUTPUT] + f"\n... [truncated]"
        return content
    except FileNotFoundError:
        return f"[ERROR] File not found: {path}"
    except Exception as e:
        return f"[ERROR] {e}"


def tool_read_image(path: str) -> dict:
    """Read an image file, return as base64 dict for multimodal injection."""
    path = os.path.expanduser(path)
    print(f"\n  🖼️  read_image: {path}")
    try:
        ext = Path(path).suffix.lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        return {"type": "image", "mime": mime, "data": b64, "path": path}
    except FileNotFoundError:
        return {"type": "error", "message": f"Image not found: {path}"}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def tool_write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed."""
    path = os.path.expanduser(path)
    print(f"\n  ✏️  write_file: {path} ({len(content)} chars)")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"[ERROR] {e}"


def tool_edit_file(path: str, old_str: str, new_str: str) -> str:
    """Replace a unique string in a file."""
    path = os.path.expanduser(path)
    print(f"\n  🔄 edit_file: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_str)
        if count == 0:
            return f"[ERROR] old_str not found in {path}"
        if count > 1:
            return f"[ERROR] old_str appears {count} times in {path} — must be unique"

        new_content = content.replace(old_str, new_str, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Edit applied to {path}"
    except FileNotFoundError:
        return f"[ERROR] File not found: {path}"
    except Exception as e:
        return f"[ERROR] {e}"


def tool_glob(pattern: str) -> str:
    """List files matching a glob pattern."""
    pattern = os.path.expanduser(pattern)
    print(f"\n  🔍 glob: {pattern}")
    try:
        matches = glob_module.glob(pattern, recursive=True)
        if not matches:
            return f"No files matched: {pattern}"
        return "\n".join(sorted(matches))
    except Exception as e:
        return f"[ERROR] {e}"


def tool_grep(pattern: str, path: str, recursive: bool = True) -> str:
    """Search for a pattern in files."""
    path = os.path.expanduser(path)
    print(f"\n  🔎 grep: '{pattern}' in {path}")
    try:
        cmd = ["grep", "-n"]
        if recursive:
            cmd.append("-r")
        cmd += [pattern, path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout
        if not output:
            return f"No matches for '{pattern}' in {path}"
        if len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + "\n... [truncated]"
        return output
    except Exception as e:
        return f"[ERROR] {e}"


# ─────────────────────────────────────────────
# TOOL DISPATCHER
# ─────────────────────────────────────────────

def _extract_png_from_output(text: str, render_start_time: float | None = None) -> dict | None:
    """
    Find a render preview PNG from bash output and return a tool_read_image payload.

    render_start_time:
        Unix timestamp captured immediately before openscad-render.sh was invoked.
        Used to filter out stale PNGs from previous iterations. If None, no
        filtering is applied (legacy behaviour).

    Three strategies, in order:

    1. Token scan — look for any whitespace-separated token ending in .png
       (handles "Saved to: /path/preview.png" and bare paths).
       If render_start_time is provided, the file's mtime must be >= it.

    2. Directory fallback — the openscad-render.sh script prints:
         "Preview images saved in: /home/user/.../previews/"
       No individual .png token appears, so we glob that directory and
       pick the file from THIS render based on mtime, not alphabetical order.

    3. Mtime cutoff (defensive) — if all the above fail but a render_start_time
       is provided, scan common preview locations for any PNG with mtime
       >= render_start_time.
    """
    cutoff = (render_start_time - RENDER_MTIME_SLACK) if render_start_time else None

    def _accept_mtime(p: str) -> bool:
        """True if file is fresh enough to be from this render."""
        if cutoff is None:
            return True
        try:
            return os.path.getmtime(p) >= cutoff
        except OSError:
            return False

    # Strategy 1: token scan for a direct .png path
    for raw_line in text.splitlines():
        for token in raw_line.split():
            token_clean = token.rstrip(".,;:")
            if token_clean.endswith(".png"):
                expanded = os.path.expanduser(token_clean)
                if os.path.exists(expanded) and _accept_mtime(expanded):
                    result = tool_read_image(expanded)
                    if result["type"] == "image":
                        return result

    # Strategy 2: directory glob with mtime filtering
    # Matches lines like: "Preview images saved in: /home/xr23/.../previews/"
    for raw_line in text.splitlines():
        for token in raw_line.split():
            token_clean = token.rstrip(".,;:")
            expanded = os.path.expanduser(token_clean)
            if os.path.isdir(expanded):
                pngs = glob_module.glob(os.path.join(expanded, "*.png"))
                if not pngs:
                    continue

                # Filter to only PNGs from this render (if we have a snapshot)
                fresh = [p for p in pngs if _accept_mtime(p)]

                if not fresh:
                    # The directory exists and has PNGs, but none are fresh.
                    # This means render produced output elsewhere, or failed silently.
                    # Don't fall back to stale files — leave for strategy 3.
                    if cutoff is not None:
                        print(
                            f"\n  ⚠️  [auto-dir] {len(pngs)} PNGs in {expanded} but "
                            f"none modified since render started — skipping"
                        )
                    continue

                # Among fresh files, prefer isometric view; otherwise newest.
                isometric = [p for p in fresh if "isometric" in os.path.basename(p)]
                chosen = (
                    max(isometric, key=os.path.getmtime) if isometric
                    else max(fresh, key=os.path.getmtime)
                )
                result = tool_read_image(chosen)
                if result["type"] == "image":
                    print(f"\n  🖼️  [auto-dir] loaded from previews dir: {chosen}")
                    return result

    # Strategy 3: last-resort defensive scan
    # If we have a render_start_time but neither strategy above found anything,
    # scan a few common locations for any PNG newer than the snapshot.
    if cutoff is not None:
        candidate_globs = [
            os.path.expanduser("~/openscad-projects/**/previews/*.png"),
        ]
        candidates: list[str] = []
        for pat in candidate_globs:
            candidates.extend(glob_module.glob(pat, recursive=True))

        fresh = [p for p in candidates if _accept_mtime(p)]
        if fresh:
            isometric = [p for p in fresh if "isometric" in os.path.basename(p)]
            chosen = (
                max(isometric, key=os.path.getmtime) if isometric
                else max(fresh, key=os.path.getmtime)
            )
            result = tool_read_image(chosen)
            if result["type"] == "image":
                print(f"\n  🖼️  [auto-scan] fallback located fresh render: {chosen}")
                return result

    return None


def dispatch_tool(
    name: str,
    args: dict,
    active_project_path: str = "",
    write_counts: dict = None,          # Fix 3: tracks writes per path since last render
) -> tuple[str, dict | None, str, bool, bool]:
    """
    Execute a tool call.

    Returns:
      text_result             — string to append as role:tool message
      image_payload_or_None   — dict with type/mime/data if an image was loaded
      active_project_path     — updated project path (set on first successful init)
      is_blocked_init         — True signals the loop to escalate init blocking
      needs_write_escalation  — True signals the loop to inject a render escalation
    """
    if write_counts is None:
        write_counts = {}

    if name == "bash":
        command = args.get("command", "")

        # Block further init calls once a project is active.
        if "openscad-project.sh init" in command and active_project_path:
            forced = (
                f"[HARNESS] BLOCKED — a project was already successfully initialized "
                f"this turn at: {active_project_path}. "
                "You MUST NOT call init again. "
                "Your only valid next action is write_file to create "
                f"{active_project_path}/src/main.scad with the OpenSCAD code."
            )
            print(f"\n  🚫 [harness] blocked redundant init — active project: {active_project_path}")
            return forced, None, active_project_path, True, False

        # Fix 4: snapshot the wall-clock time immediately before any render.
        # We capture it BEFORE tool_bash so any PNG produced by the render
        # will have mtime >= render_start_time (minus FS resolution slack).
        render_start_time = None
        if "openscad-render.sh" in command:
            render_start_time = time.time()
            print(f"\n  ⏱️  [harness] render snapshot: {render_start_time:.3f}")

        text_result = tool_bash(command, args.get("working_dir"))

        # Project init "already exists" fix.
        if "openscad-project.sh init" in command and (
            "already exists" in text_result.lower()
            or "project exists" in text_result.lower()
        ):
            project_path = ""
            for line in text_result.splitlines():
                for token in line.split():
                    expanded = os.path.expanduser(token)
                    if os.path.isdir(expanded) and "openscad-projects" in expanded:
                        project_path = expanded
                        break

            text_result += (
                "\n[HARNESS] SUCCESS — project already exists, no action needed. "
                f"Project is at: {project_path or '~/openscad-projects/<name>'}. "
                "IGNORE the line saying 'choose a different name' — that is for humans only. "
                "You MUST use this existing project. "
                "Do NOT call init again under any name. "
                "Your next step is write_file to create src/main.scad inside this project."
            )
            if project_path:
                active_project_path = project_path

        # Detect a fresh successful init and lock in the project path.
        if "openscad-project.sh init" in command and not active_project_path:
            for line in text_result.splitlines():
                for token in line.split():
                    expanded = os.path.expanduser(token.rstrip(".,;:"))
                    if os.path.isdir(expanded) and "openscad-projects" in expanded:
                        active_project_path = expanded
                        text_result += (
                            f"\n[HARNESS] SUCCESS — project created at: {active_project_path}. "
                            "Do NOT call init again. "
                            "Your only next action is write_file to create "
                            f"{active_project_path}/src/main.scad with the OpenSCAD code."
                        )
                        print(f"\n  ✅ [harness] project locked in: {active_project_path}")
                        break
                if active_project_path:
                    break

        # Fix 3: a render call resets the write counter for any .scad file.
        # This is the only legitimate reason to keep rewriting — the model
        # saw a PNG, decided something was wrong, and wants to fix it.
        image_payload = None
        if "openscad-render.sh" in command:
            # Reset write counts for any .scad files (render = visual feedback received)
            keys_to_reset = [k for k in write_counts if k.endswith(".scad")]
            for k in keys_to_reset:
                write_counts[k] = 0
            print(f"\n  🔄 [harness] render call — write counters reset for .scad files")

            # Fix 4: pass render_start_time to filter out stale PNGs.
            image_payload = _extract_png_from_output(text_result, render_start_time)
            if image_payload:
                print(f"\n  🖼️  [auto] render preview loaded: {image_payload['path']}")
            else:
                print(
                    "\n  ⚠️  [auto] render ran but no fresh .png was located "
                    "— model will not receive a visual this iteration"
                )

        return text_result, image_payload, active_project_path, False, False

    elif name == "read_file":
        return tool_read_file(args["path"]), None, active_project_path, False, False

    elif name == "read_image":
        result = tool_read_image(args["path"])
        if result["type"] == "error":
            return f"[ERROR] {result['message']}", None, active_project_path, False, False
        return f"[Image loaded: {result['path']}]", result, active_project_path, False, False

    elif name == "write_file":
        path = os.path.expanduser(args.get("path", ""))
        text_result = tool_write_file(path, args["content"])

        # Fix 3: track consecutive writes to .scad files without a render.
        needs_write_escalation = False
        if path.endswith(".scad"):
            write_counts[path] = write_counts.get(path, 0) + 1
            count = write_counts[path]
            print(f"\n  📊 [harness] write count for {os.path.basename(path)}: {count}")

            if count >= WRITE_ESCALATE_THRESHOLD:
                # Signal the loop to inject a user-role escalation message
                needs_write_escalation = True
                print(f"\n  🔴 [harness] write escalation threshold reached ({count} writes) — forcing render")
            elif count >= WRITE_WARN_THRESHOLD:
                # Softer warning appended to the tool result
                text_result += (
                    f"\n[HARNESS] WARNING — you have written this file {count} times "
                    "without rendering it. "
                    "You are rewriting blindly with no visual feedback. "
                    "STOP rewriting and call openscad-render.sh preview on this file NOW "
                    "so you can see what the model looks like before making further changes."
                )
                print(f"\n  🟡 [harness] write warning appended ({count} writes without render)")

        return text_result, None, active_project_path, False, needs_write_escalation

    elif name == "edit_file":
        return tool_edit_file(args["path"], args["old_str"], args["new_str"]), None, active_project_path, False, False

    elif name == "glob":
        return tool_glob(args["pattern"]), None, active_project_path, False, False

    elif name == "grep":
        return tool_grep(args["pattern"], args["path"], args.get("recursive", True)), None, active_project_path, False, False

    else:
        return f"[ERROR] Unknown tool: {name}", None, active_project_path, False, False


# ─────────────────────────────────────────────
# SKILL LOADER
# ─────────────────────────────────────────────

def load_skill(skill_path: Path) -> str:
    """Load SKILL.md, strip YAML frontmatter, return as system prompt."""
    skill_file = skill_path / SKILL_FILE
    if not skill_file.exists():
        print(f"[WARN] SKILL.md not found at {skill_file}. Running without skill context.")
        return "You are a helpful 3D CAD assistant using OpenSCAD."

    raw = skill_file.read_text(encoding="utf-8")

    # Strip YAML frontmatter (--- ... ---)
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            content = parts[2].strip()
        else:
            content = raw
    else:
        content = raw

    # Patch skill paths to use actual skill_path
    content = content.replace(
        "~/.claude/skills/openscad/scripts/",
        str(skill_path / "scripts") + "/"
    ).replace(
        "~/.claude/skills/openscad/templates/",
        str(skill_path / "templates") + "/"
    ).replace(
        "~/.claude/skills/openscad/references/",
        str(skill_path / "references") + "/"
    )

    return content


# ─────────────────────────────────────────────
# AGENTIC LOOP
# ─────────────────────────────────────────────

def run_agent(model: str, skill_path: Path, max_iterations: int = 50) -> None:
    """
    Main agentic loop.
    Sends messages to Ollama, handles tool calls, feeds results back,
    repeats until the model returns a final text response.
    """
    system_prompt = load_skill(skill_path)
    messages = [{"role": "system", "content": system_prompt}]

    print("\n" + "═" * 60)
    print("  OpenSCAD Skill Harness  —  Ollama / Qwen3-VL")
    print(f"  Model      : {model}")
    print(f"  Skill path : {skill_path}")
    print("═" * 60)
    print("  Type your request. 'quit' or Ctrl-C to exit.\n")

    # ── Outer conversation loop ──────────────────────────────
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye.")
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # ── Inner agentic loop ───────────────────────────────
        iteration = 0
        pending_images: list[dict] = []
        active_project_path: str = ""
        consecutive_blocked_inits: int = 0
        BLOCKED_INIT_ESCALATION_THRESHOLD = 2

        # Fix 3: tracks how many times each .scad file has been written
        # since the last render. Resets to 0 when a render call is made.
        # Key: absolute path, Value: write count since last render.
        write_counts: dict[str, int] = {}

        while iteration < max_iterations:
            iteration += 1

            if pending_images:
                messages.append({
                    "role": "user",
                    "content": (
                        f"[{len(pending_images)} image(s) attached — "
                        "please analyze them and continue.]"
                    ),
                    "images": [img["data"] for img in pending_images],
                })
                pending_images = []

            print(f"\n  [→ Ollama, iteration {iteration}]")
            try:
                response = ollama.chat(
                    model=model,
                    messages=messages,
                    tools=TOOLS,
                    options={"num_ctx": 32768},
                )

            except ollama.ResponseError as e:
                print(f"\n[Ollama ERROR] {e}")
                break
            except Exception as e:
                print(f"\n[ERROR] Unexpected error calling Ollama: {e}")
                break

            raw_msg = response.message if hasattr(response, "message") else response["message"]

            if hasattr(raw_msg, "role"):
                msg = {
                    "role": raw_msg.role,
                    "content": raw_msg.content or "",
                    "tool_calls": raw_msg.tool_calls or []
                }
            else:
                msg = raw_msg

            messages.append({"role": msg["role"], "content": msg.get("content", "")})

            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final = msg.get("content", "").strip()
                print(f"\nAssistant: {final}\n")
                break

            for call in tool_calls:
                if hasattr(call, "function"):
                    fn   = call.function.name
                    args = call.function.arguments
                else:
                    fn   = call["function"]["name"]
                    args = call["function"]["arguments"]

                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                text_result, image_payload, active_project_path, is_blocked_init, needs_write_escalation = dispatch_tool(
                    fn, args, active_project_path, write_counts
                )

                messages.append({
                    "role": "tool",
                    "content": text_result
                })

                if image_payload:
                    pending_images.append(image_payload)

                # Init escalation (existing logic)
                if is_blocked_init:
                    consecutive_blocked_inits += 1
                    if consecutive_blocked_inits >= BLOCKED_INIT_ESCALATION_THRESHOLD:
                        escalation = (
                            f"You have tried to initialize a project {consecutive_blocked_inits} "
                            f"times after it was already created. STOP. "
                            f"The project is at {active_project_path}. "
                            f"Call write_file NOW with path "
                            f"{active_project_path}/src/main.scad "
                            f"and write the complete OpenSCAD code for the ring design."
                        )
                        messages.append({"role": "user", "content": escalation})
                        print(f"\n  🔴 [harness] escalated to user-role message after "
                              f"{consecutive_blocked_inits} blocked inits")
                else:
                    consecutive_blocked_inits = 0

                # Fix 3: write loop escalation — inject a user-role message
                # forcing the model to render instead of rewriting again.
                if needs_write_escalation:
                    scad_path = os.path.expanduser(args.get("path", ""))
                    escalation = (
                        f"[HARNESS] STOP. You have rewritten {os.path.basename(scad_path)} "
                        f"{write_counts.get(scad_path, '?')} times without ever rendering it. "
                        "Rewriting without visual feedback is pointless — you cannot know if "
                        "the code is correct without seeing the result. "
                        f"Call bash NOW with command: "
                        f"bash ~/.claude/skills/openscad/scripts/openscad-render.sh preview {scad_path} "
                        "Then analyse the preview image before making any further changes."
                    )
                    messages.append({"role": "user", "content": escalation})
                    print(f"\n  🔴 [harness] write escalation injected as user-role message")

        else:
            print(f"\n[WARN] Reached max iterations ({max_iterations}). Stopping agent loop.")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OpenSCAD Skill Harness for Ollama",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python3 openscad_ollama_harness.py
              python3 openscad_ollama_harness.py --model qwen3-vl:235b-cloud
              python3 openscad_ollama_harness.py --skill-path ~/my-skills/openscad
        """)
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model tag (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--skill-path",
        type=Path,
        default=DEFAULT_SKILL_PATH,
        help=f"Path to the OpenSCAD skill directory (default: {DEFAULT_SKILL_PATH})"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=50,
        help="Max tool-call iterations per user turn (default: 50)"
    )
    args = parser.parse_args()

    try:
        list_response = ollama.list()
        if hasattr(list_response, "models"):
            models = [
                getattr(m, "model", None) or getattr(m, "name", None)
                for m in list_response.models
            ]
        else:
            models = [
                m.get("name") or m.get("model")
                for m in list_response.get("models", [])
            ]
        models = [m for m in models if m]

        if args.model not in models:
            print(f"[WARN] Model '{args.model}' not found locally in Ollama.")
            print(f"  Available models: {', '.join(models) or 'none'}")
            print(f"  Pull it with:  ollama pull {args.model}")
            print("  Continuing anyway — Ollama may still serve it remotely.\n")
        else:
            print(f"  ✓ Model '{args.model}' found.\n")
    except Exception as e:
        print(f"[WARN] Could not verify Ollama model list: {e}")
        print("  If 'ollama serve' says port 11434 is already in use, Ollama is running — that is fine.\n")

    run_agent(
        model=args.model,
        skill_path=args.skill_path,
        max_iterations=args.max_iterations,
    )


if __name__ == "__main__":
    main()