# openscad_ollama_harness.py — Explained

A line-by-line walkthrough of what the script does and how every part works.

---

## What It Is

`openscad_ollama_harness.py` is the **agentic runtime** that connects the Ollama language model (`qwen3-vl:235b-cloud`) to the OpenSCAD skill's scripts and your filesystem.

The original OpenSCAD skill was built for **Claude Code**, which is not just an LLM — it is an LLM bundled with a full agent runtime: it can run shell commands, read and write files, and loop through tool calls automatically. Ollama gives you only the LLM. This script is everything else.

In one sentence: **the harness is the glue between what the model wants to do and your OS actually doing it.**

---

## High-Level Structure

The script has five distinct sections, each with a single responsibility:

```
openscad_ollama_harness.py
│
├── CONFIG                  Constants: model name, skill path, safety blocklist
├── TOOLS (schemas)         JSON descriptions of every tool, sent to Ollama
├── TOOL IMPLEMENTATIONS    Python functions that actually execute each tool
├── TOOL DISPATCHER         Routes a tool name to its implementation
├── SKILL LOADER            Reads SKILL.md and prepares it as a system prompt
└── AGENTIC LOOP            The main conversation + tool-call orchestration
```

---

## Section 1 — CONFIG

```python
DEFAULT_MODEL      = "qwen3-vl:235b-cloud"
DEFAULT_SKILL_PATH = Path.home() / ".claude" / "skills" / "openscad"
SKILL_FILE         = "SKILL.md"

BLOCKED_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",   # fork bomb
]

MAX_TOOL_OUTPUT = 8_000
```

**What it does:** Sets global constants used throughout the script.

`BLOCKED_COMMANDS` is a safety blocklist. Before the harness runs any shell command, it scans the command string against every entry in this list. If there is a match, the command is refused and a `[BLOCKED]` message is returned to the model instead. This prevents the model from accidentally or maliciously destroying the system.

`MAX_TOOL_OUTPUT` caps every tool's text output at 8,000 characters. This is important because tool outputs get appended to the message history that is sent back to Ollama on every call. Without a cap, a single `bash` command that produces megabytes of output would overflow the context window (`num_ctx = 32768` tokens) and crash or degrade the model's reasoning.

---

## Section 2 — TOOLS (schemas)

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command and return its stdout + stderr...",
            "parameters": { ... }
        }
    },
    # ... read_file, read_image, write_file, edit_file, glob, grep
]
```

**What it does:** Defines the seven tools that the model is allowed to call, in the JSON schema format that Ollama expects.

This list is passed directly to `ollama.chat(..., tools=TOOLS)` on every call. The model reads it to understand what capabilities it has. Specifically, it reads the `description` field of each tool to decide *when* to use it, and the `parameters` schema to know *what arguments* to pass.

The model never executes tools itself. It only outputs a `tool_call` block — a structured JSON object saying "I want to call this tool with these arguments." The harness intercepts that, runs the actual tool, and feeds the result back.

**The seven tools:**

| Tool | What it tells the model |
|---|---|
| `bash` | Run any shell command, get stdout + stderr back |
| `read_file` | Read a text file (.scad, .md, .json, logs) |
| `read_image` | Read a PNG or JPEG for visual analysis |
| `write_file` | Create or overwrite a file with given content |
| `edit_file` | Replace a unique string in a file (surgical edits) |
| `glob` | List files matching a pattern |
| `grep` | Search for a string or regex inside files |

---

## Section 3 — TOOL IMPLEMENTATIONS

These are the Python functions that do the actual work when the model calls a tool.

### `tool_bash(command, working_dir)`

```python
result = subprocess.run(
    command,
    shell=True,
    capture_output=True,
    text=True,
    cwd=working_dir or os.getcwd(),
    timeout=300
)
```

Runs any shell command using Python's `subprocess` module. `shell=True` means the command is passed to `/bin/sh` exactly as written — the same as typing it in a terminal. `capture_output=True` captures both stdout and stderr so they can be returned to the model as text. The 300-second timeout prevents OpenSCAD renders (which can be slow) from hanging the process forever.

Both stdout and stderr are combined into a single string and returned. If the exit code is non-zero, the exit code is appended so the model knows the command failed.

### `tool_read_file(path)`

```python
with open(path, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()
```

Opens a text file and returns its content. `errors="replace"` means broken or non-UTF-8 bytes are replaced with `?` instead of crashing. Used by the model to read `.scad` source files, reference markdown documents, JSON analysis outputs, and error logs.

### `tool_read_image(path)`

```python
ext = Path(path).suffix.lower()
mime = "image/png" if ext == ".png" else "image/jpeg"
with open(path, "rb") as f:
    b64 = base64.standard_b64encode(f.read()).decode()
return {"type": "image", "mime": mime, "data": b64, "path": path}
```

This one is different from all the others. It does not return a plain string. Instead it returns a dict containing the image encoded as a base64 string, along with its MIME type. This is because Ollama's vision API does not accept raw file paths — it requires images to be embedded directly in the message as base64 data URIs.

The return value is not immediately appended to the message history. It is placed in a `pending_images` queue and injected in the next loop iteration as a multimodal message (see the agentic loop section for how this works).

### `tool_write_file(path, content)`

```python
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
```

Writes a file, creating any intermediate directories first (`exist_ok=True` means it won't fail if they already exist). The model uses this to write new `.scad` source files.

### `tool_edit_file(path, old_str, new_str)`

```python
count = content.count(old_str)
if count == 0:
    return f"[ERROR] old_str not found in {path}"
if count > 1:
    return f"[ERROR] old_str appears {count} times in {path} — must be unique"

new_content = content.replace(old_str, new_str, 1)
```

A surgical string replacement that enforces uniqueness before applying the edit. If `old_str` appears more than once in the file, the edit is refused — this prevents the model from accidentally changing the wrong occurrence. Used for iterative refinement of `.scad` files (e.g. changing a single dimension variable without rewriting the whole file).

### `tool_glob(pattern)`

```python
matches = glob_module.glob(pattern, recursive=True)
return "\n".join(sorted(matches))
```

File discovery using shell glob patterns (`*`, `**`, `?`). The `recursive=True` flag enables the `**` wildcard to match across subdirectories. The model uses this to check whether a project directory exists, list available preview images, or discover `.scad` files.

### `tool_grep(pattern, path, recursive)`

```python
cmd = ["grep", "-n"]
if recursive:
    cmd.append("-r")
cmd += [pattern, path]
result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
```

Delegates to the system's `grep` binary. The `-n` flag includes line numbers in the output. Used by the model to locate specific variable definitions, module names, or error strings inside `.scad` files.

---

## Section 4 — TOOL DISPATCHER

```python
def dispatch_tool(name: str, args: dict) -> tuple[str, dict | None]:
    if name == "bash":
        return tool_bash(args.get("command", ""), args.get("working_dir")), None
    elif name == "read_image":
        result = tool_read_image(args["path"])
        if result["type"] == "error":
            return f"[ERROR] {result['message']}", None
        return f"[Image loaded: {result['path']}]", result
    # ... etc
```

**What it does:** A single routing function that maps a tool name string to its implementation. It also handles the special two-return-value case for `read_image`, which returns both a text acknowledgment (for the message history) and the actual image payload (for the pending queue).

Every other tool returns `(text_string, None)`. Only `read_image` returns `(text_string, image_dict)`. The `None` vs `image_dict` distinction is how the agentic loop knows whether to queue an image.

---

## Section 5 — SKILL LOADER

```python
def load_skill(skill_path: Path) -> str:
    raw = skill_file.read_text(encoding="utf-8")

    # Strip YAML frontmatter
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        content = parts[2].strip()

    # Patch hardcoded paths
    content = content.replace(
        "~/.claude/skills/openscad/scripts/",
        str(skill_path / "scripts") + "/"
    )
    return content
```

**What it does:** Reads `SKILL.md` and prepares it to be used as the model's system prompt.

It does two things to the raw file content:

**Strips the YAML frontmatter.** The top of `SKILL.md` contains a metadata block between `---` delimiters (`name`, `description`, `allowed-tools`, etc.). That metadata was written for Claude Code's skill router — it has no meaning to Ollama. The loader splits the file on `---` and takes only the third part (the actual content after the header).

**Patches hardcoded paths.** The skill was written assuming it would always be installed at `~/.claude/skills/openscad/`. The loader replaces those hardcoded paths with the actual `--skill-path` argument the user provided. This allows the skill to be installed anywhere on disk.

The returned string (the full 917-line workflow document) becomes the system prompt — the first message in every conversation.

---

## Section 6 — AGENTIC LOOP

This is the core of the harness. It contains two nested loops.

### The Outer Loop — Conversation Turns

```python
while True:
    user_input = input("You: ").strip()
    messages.append({"role": "user", "content": user_input})
    # ... run inner loop
```

Waits for user input, appends it to the `messages` list, then hands off to the inner loop. The `messages` list is the entire conversation history — every user message, every assistant response, and every tool result. It is passed to Ollama on every call so the model has full context of what has happened.

The outer loop runs until the user types `quit`, `exit`, or `q`, or presses `Ctrl-C`.

### The Inner Loop — Tool Call Iterations

```python
while iteration < max_iterations:
    iteration += 1

    # 1. Inject pending images (if any)
    if pending_images:
        messages.append({"role": "user", "content": [image_parts...]})
        pending_images = []

    # 2. Call Ollama
    response = ollama.chat(model=model, messages=messages, tools=TOOLS)

    # 3. Parse response
    ...

    # 4. If no tool calls → print final answer, break
    if not tool_calls:
        print(f"\nAssistant: {final}")
        break

    # 5. Execute each tool call, append results
    for call in tool_calls:
        text_result, image_payload = dispatch_tool(fn, args)
        messages.append({"role": "tool", "content": text_result})
        if image_payload:
            pending_images.append(image_payload)
```

This loop runs until the model stops calling tools and returns a plain text response. Each iteration:

**Step 1 — Image injection.** If the previous iteration called `read_image`, the image payload is sitting in `pending_images`. Before calling Ollama again, the loop constructs a multimodal message — a user-role message containing base64 image data plus the text "Analyze this preview." — and appends it to `messages`. This is the mechanism that gives the model visual feedback from rendered PNGs.

The reason images are queued and injected on the *next* iteration rather than immediately is that Ollama's API does not allow a `tool_result` message to contain image data — tool results must be plain text. The image has to be re-injected as a fresh user message, which requires starting a new model call.

**Step 2 — Ollama call.** Sends the full message history plus the `TOOLS` schema to Ollama. `num_ctx: 32768` sets the context window to 32,768 tokens.

**Step 3 — Response normalisation.** The Ollama Python SDK changed its response format between versions. The old SDK (before 0.2.0) returned plain dicts (`response["message"]`). The new SDK returns typed objects (`response.message`). The harness checks for both using `hasattr()` and normalises to a plain dict either way, so the rest of the code works regardless of which SDK version is installed.

**Step 4 — Exit condition.** If the model's response contains no `tool_calls`, it has finished reasoning and is returning its final answer. The answer is printed and the inner loop breaks, returning to the outer loop to wait for the next user input.

**Step 5 — Tool execution.** If there are tool calls, each one is dispatched, executed, and its result appended to `messages` as a `role: tool` message. Images are queued in `pending_images`. Then the loop continues — the next iteration calls Ollama again with the updated message history, which now includes the tool results.

### The Iteration Cap

```python
while iteration < max_iterations:
    ...
else:
    print(f"\n[WARN] Reached max iterations ({max_iterations}). Stopping.")
```

The `else` clause on a `while` loop executes when the loop condition becomes false (i.e. when `max_iterations` is reached without a `break`). This is the safeguard against infinite loops — if the model keeps calling tools without ever returning a final answer, the harness stops it after 50 iterations (default) and prints a warning.

---

## Section 7 — ENTRY POINT (`main`)

```python
parser = argparse.ArgumentParser(...)
parser.add_argument("--model", ...)
parser.add_argument("--skill-path", ...)
parser.add_argument("--max-iterations", ...)
```

Standard argument parsing. Three CLI flags, all optional with sensible defaults.

```python
list_response = ollama.list()
if hasattr(list_response, "models"):       # new SDK
    models = [getattr(m, "model", None) ... for m in list_response.models]
else:                                       # old SDK
    models = [m.get("name") ... for m in list_response.get("models", [])]
```

Before starting the loop, the script tries to verify that the requested model is available locally in Ollama. This uses the same dual-SDK-version pattern as the response normalisation — checking `hasattr` first to decide whether to use attribute access or dict access.

If the model is not found locally, a warning is printed but execution continues. The model may still be served remotely by Ollama even if it does not appear in the local list.

---

## End-to-End Flow for a Single Request

Putting it all together, here is what happens from the moment you type a message to the moment the assistant replies:

```
You type:  "Design a 60x40x20mm box with 2mm walls"
                │
                ▼
  messages = [system: SKILL.md, user: "Design a 60x40x20mm box..."]
                │
                ▼
  ollama.chat(messages, tools=TOOLS)
                │
                ▼
  Model decides: call bash("openscad-project.sh init box")
                │
                ▼
  dispatch_tool → tool_bash → subprocess.run → "Project created at ~/openscad-projects/box/"
                │
                ▼
  messages += [tool: "Project created..."]
                │
                ▼
  ollama.chat(messages, tools=TOOLS)   ← iteration 2
                │
                ▼
  Model decides: call write_file("~/openscad-projects/box/src/main.scad", "...")
                │
                ▼
  dispatch_tool → tool_write_file → open(path, 'w').write(content) → "Written 812 chars"
                │
                ▼
  messages += [tool: "Written 812 chars"]
                │
                ▼
  [more iterations: render, read_image, possibly edit and re-render...]
                │
                ▼
  Model returns no tool_calls → final text answer
                │
                ▼
  Assistant: "Your box is ready. Preview at ~/openscad-projects/box/previews/iso.png..."
```

Each iteration the message history grows by one tool call result. The model always sees the full history, so it knows what has already been done and what remains.

---

## Quick Reference

| Symbol in console | Meaning |
|---|---|
| `[→ Ollama, iteration N]` | A new call to the model is being made |
| `🔧 bash: ...` | A shell command is being run |
| `📄 read_file: ...` | A text file is being read |
| `🖼️  read_image: ...` | An image is being encoded for vision |
| `✏️  write_file: ...` | A file is being created or overwritten |
| `🔄 edit_file: ...` | A targeted string replacement is being applied |
| `🔍 glob: ...` | A file pattern search is running |
| `🔎 grep: ...` | A text search is running |
| `[BLOCKED]` | A dangerous command was refused |
| `[WARN]` | Non-fatal warning (Ollama connectivity, iteration cap) |
| `[Ollama ERROR]` | The model returned an API error |
| `Assistant: ...` | The model's final answer for this turn |