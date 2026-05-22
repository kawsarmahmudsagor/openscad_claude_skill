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

Fix (2025-05): Corrected multimodal image injection.
  Ollama SDK enforces Message.content as str; images must be passed via
  the top-level images= parameter of ollama.chat(), not as OpenAI-style
  content list dicts.
"""

import argparse
import base64
import glob as glob_module
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import ollama  # pip install ollama


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

DEFAULT_MODEL      = "gemma4:31b"
DEFAULT_SKILL_PATH = Path.home() / ".claude" / "skills" / "openscad"
SKILL_FILE         = "SKILL.md"

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

def dispatch_tool(name: str, args: dict) -> tuple[str, dict | None]:
    """
    Execute a tool call.
    Returns (text_result, image_payload_or_None).
    image_payload is a dict with type/mime/data if read_image was called.
    """
    if name == "bash":
        return tool_bash(args.get("command", ""), args.get("working_dir")), None

    elif name == "read_file":
        return tool_read_file(args["path"]), None

    elif name == "read_image":
        result = tool_read_image(args["path"])
        if result["type"] == "error":
            return f"[ERROR] {result['message']}", None
        return f"[Image loaded: {result['path']}]", result

    elif name == "write_file":
        return tool_write_file(args["path"], args["content"]), None

    elif name == "edit_file":
        return tool_edit_file(args["path"], args["old_str"], args["new_str"]), None

    elif name == "glob":
        return tool_glob(args["pattern"]), None

    elif name == "grep":
        return tool_grep(args["pattern"], args["path"], args.get("recursive", True)), None

    else:
        return f"[ERROR] Unknown tool: {name}", None


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
        # Stores dicts with keys: type, mime, data, path
        pending_images: list[dict] = []

        while iteration < max_iterations:
            iteration += 1

            # If images are pending, embed them inside a user message dict.
            # The Ollama SDK expects images as a key *within* the message,
            # not as a top-level ollama.chat() argument.
            # Value: list of raw base64 strings (no data-URI prefix needed).
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

            # Call Ollama
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

            # --- Normalise response for both SDK versions ---
            # New SDK (>=0.2.0): ChatResponse object with .message attribute
            # Old SDK: plain dict with ["message"] key
            raw_msg = response.message if hasattr(response, "message") else response["message"]

            # Normalise the message itself into a plain dict for messages history
            if hasattr(raw_msg, "role"):
                msg = {
                    "role": raw_msg.role,
                    "content": raw_msg.content or "",
                    "tool_calls": raw_msg.tool_calls or []
                }
            else:
                msg = raw_msg  # already a dict

            messages.append({"role": msg["role"], "content": msg.get("content", "")})

            tool_calls = msg.get("tool_calls") or []

            # No tool calls → model is done, print final answer
            if not tool_calls:
                final = msg.get("content", "").strip()
                print(f"\nAssistant: {final}\n")
                break

            # Execute each tool call
            for call in tool_calls:
                # New SDK: ToolCall object with .function attribute
                # Old SDK: plain dict with ["function"] key
                if hasattr(call, "function"):
                    fn   = call.function.name
                    args = call.function.arguments
                else:
                    fn   = call["function"]["name"]
                    args = call["function"]["arguments"]

                # args may come as a JSON string (some Ollama versions)
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                text_result, image_payload = dispatch_tool(fn, args)

                # Append tool result to messages
                messages.append({
                    "role": "tool",
                    "content": text_result
                })

                # Queue any image for multimodal injection on the next LLM call
                if image_payload:
                    pending_images.append(image_payload)

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

    # Verify Ollama is reachable and model exists.
    # Handles both old SDK (plain dict) and new SDK >=0.2.0 (typed objects).
    try:
        list_response = ollama.list()
        if hasattr(list_response, "models"):            # new SDK: ListResponse object
            models = [
                getattr(m, "model", None) or getattr(m, "name", None)
                for m in list_response.models
            ]
        else:                                           # old SDK: plain dict
            models = [
                m.get("name") or m.get("model")
                for m in list_response.get("models", [])
            ]
        models = [m for m in models if m]              # drop any None entries

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