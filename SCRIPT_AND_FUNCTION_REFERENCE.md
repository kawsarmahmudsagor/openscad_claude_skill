# OpenSCAD Claude Skill — Script & Function Reference

> Complete documentation of every script, Python module, template, and reference file in the repo.

---

## Table of Contents

1. [Shell Scripts](#shell-scripts)
   - [openscad-render.sh](#openscad-rendersh)
   - [openscad-project.sh](#openscad-projectsh)
   - [openscad-validate.sh](#openscad-validatesh)
   - [openscad-stl-analyze.sh](#openscad-stl-analyzesh)
   - [openscad-stl-reconstruct.sh](#openscad-stl-reconstructsh)
   - [openscad-stl-compare.sh](#openscad-stl-comparesh)
2. [Python Scripts](#python-scripts)
   - [openscad-sdf-optimize.py](#openscad-sdf-optimizepy)
   - [openscad-adaptive-slice.py](#openscad-adaptive-slicepy)
   - [openscad-profile-extract.py](#openscad-profile-extractpy)
3. [Templates](#templates)
   - [enclosure.scad](#enclosurescad)
   - [bracket.scad](#bracketscad)
   - [printable-lib.scad](#printable-libscad)
4. [References](#references)
   - [language-reference.md](#language-referencemd)
   - [reconstruction-guide.md](#reconstruction-guidemd)
5. [Skill Definition](#skill-definition)
   - [SKILL.md](#skillmd)
6. [Eval Framework](#eval-framework)
   - [eval.json](#evaljson)
   - [results.jsonl](#resultsjsonl)

---

## Shell Scripts

### `openscad-render.sh`

**Location:** `scripts/openscad-render.sh`  
**Purpose:** The central rendering and export engine. All render operations flow through this script.

#### Global variables

| Variable | Default | Description |
|---|---|---|
| `OPENSCAD` | `$OPENSCAD_BIN` or `which openscad` | Path to OpenSCAD binary |
| `IMGSIZE_PREVIEW` | `800,600` | Resolution for preview images |
| `IMGSIZE_HIRES` | `1600,1200` | Resolution for final export preview |
| `COLORSCHEME` | `DeepOcean` | OpenSCAD color scheme |

---

#### `get_project_dir(scad_file)`

Resolves the project root directory from a `.scad` file path.

- If the file lives inside a `src/` folder, returns the parent directory (the project root).
- Otherwise returns the directory containing the `.scad` file.

This allows all output (previews, STL, 3MF) to be placed relative to the project root regardless of where you call the script from.

---

#### `ensure_dirs(project_dir)`

Creates `$project_dir/previews/` and `$project_dir/output/` if they don't already exist.

---

#### `do_render(scad_file, ...openscad_args)`

The internal render wrapper. Calls OpenSCAD with the provided arguments and handles errors:

- Captures stdout + stderr into a variable.
- On non-zero exit code: prints `ERROR: OpenSCAD render failed`, extracts and flags syntax errors (`Parser error`).
- On success: prints any warnings, render timing, and facet/vertex counts.
- Returns the OpenSCAD exit code.

All other commands in this script call `do_render` internally rather than invoking OpenSCAD directly.

---

#### `cmd_quick(scad_file)`

**CLI:** `openscad-render.sh quick <file.scad>`

Renders a single isometric PNG preview. Uses `--autocenter --viewall` so the entire model fits the frame automatically. Output saved to `$project_dir/previews/quick-preview.png`.

---

#### `cmd_preview(scad_file)`

**CLI:** `openscad-render.sh preview <file.scad>`

Renders four named views in parallel and saves them with a timestamp:

| View name | Camera params | Description |
|---|---|---|
| `1-isometric` | `--autocenter --viewall` | Default isometric perspective |
| `2-front` | `--camera 0,0,0,90,0,0,0` | Orthographic front elevation |
| `3-right` | `--camera 0,0,0,90,0,90,0` | Orthographic right elevation |
| `4-top` | `--camera 0,0,0,0,0,0,0` | Orthographic top plan |

Each view is skipped and cleaned up if OpenSCAD produces an empty output file.

---

#### `cmd_stl(scad_file, ...d_flags)`

**CLI:** `openscad-render.sh stl <file.scad> [-D 'var=val' ...]`

Exports a binary STL file to `$project_dir/output/<basename>.stl`. Uses `--export-format binstl`. Reports file size in bytes. Accepts `-D` parameter override flags.

---

#### `cmd_3mf(scad_file, ...d_flags)`

**CLI:** `openscad-render.sh 3mf <file.scad> [-D 'var=val' ...]`

Exports a 3MF file to `$project_dir/output/<basename>.3mf`. Reports file size in bytes.

---

#### `cmd_export(scad_file, ...d_flags)`

**CLI:** `openscad-render.sh export <file.scad> [-D 'var=val' ...]`

Full production export: runs STL, 3MF, and a high-resolution (1600×1200) final preview PNG in sequence. Lists all output files at the end. Any step that fails (e.g., 3MF unsupported in older OpenSCAD) logs a non-fatal warning.

---

#### `cmd_analyze(scad_file)`

**CLI:** `openscad-render.sh analyze <file.scad>`

Design analysis pipeline:

1. Exports a temporary STL to gather geometry stats (triangle count, file size).
2. Captures `echo()` output from the `.scad` file (variable values, computed dimensions).
3. Renders a **bottom view** (`--camera 0,0,0,180,0,0,0`) to expose overhangs and first-layer footprint.
4. Renders a **wireframe view** (`--view edges`) to visualize mesh topology.

Both image renders fail gracefully on headless servers (logged as warnings, not errors). Cleans up the temporary STL on exit.

---

#### `cmd_custom(scad_file, ...options)`

**CLI:** `openscad-render.sh custom <file.scad> [--format ext] [--imgsize W,H] [--camera params] [--colorscheme name] [-D var=val]`

Fully customizable render. Parses flags one by one:

| Flag | Description |
|---|---|
| `--format` | Output format: `png`, `stl`, `3mf`, `amf`, `svg`, `dxf`, `pdf` |
| `--imgsize` | Image dimensions, e.g. `1920,1080` |
| `--camera` | OpenSCAD camera string: `tx,ty,tz,rx,ry,rz,dist` |
| `--colorscheme` | Any OpenSCAD built-in color scheme name |
| `-D` | Parameter override, repeatable |

Falls back to `--autocenter --viewall` if no camera is specified.

---

### `openscad-project.sh`

**Location:** `scripts/openscad-project.sh`  
**Purpose:** Scaffolding and lifecycle management for OpenSCAD projects.

**Projects root:** `~/openscad-projects/`

---

#### `cmd_init(name)`

**CLI:** `openscad-project.sh init <project-name>`

Creates a complete project directory structure:

```
~/openscad-projects/<name>/
├── src/main.scad     ← Starter template with parameters, derived dims, example module
├── output/
├── previews/
└── README.md         ← Quick command reference
```

Validates the project name to only allow `[a-zA-Z0-9_-]` characters, preventing directory traversal. Fails if the project already exists.

The generated `main.scad` includes: `width`, `height`, `depth`, `wall`, `tolerance` as variables; `$fn = 64`; derived inner dimensions; `echo()` debug output; and an example hollow box module.

---

#### `cmd_list()`

**CLI:** `openscad-project.sh list`

Scans `~/openscad-projects/` and prints a summary line for each project showing the count of `.scad` source files, exported `.stl` files, and PNG previews.

---

#### `cmd_clean(name)`

**CLI:** `openscad-project.sh clean <project-name>`

Deletes everything in `output/` and `previews/` for the named project. Source files in `src/` are preserved. Uses `rm -rf "${dir:?}/output/"*` with the `:?` guard to prevent accidentally wiping the root if the variable is empty.

---

#### `cmd_info(name)`

**CLI:** `openscad-project.sh info <project-name>`

Prints the project path, then recursively lists all `.scad` sources, all export files, and all preview PNGs.

---

### `openscad-validate.sh`

**Location:** `scripts/openscad-validate.sh`  
**Purpose:** Strict validation of `.scad` files with structured, categorized error output.

Runs OpenSCAD with three strict flags: `--check-parameters=true`, `--check-parameter-ranges=true`, `--hardwarnings`. Captures all output. Then categorizes the result into one of five error classes:

| Category | Trigger | What to do |
|---|---|---|
| `SYNTAX_ERROR` | Output contains "Parser error" | Check the reported line number; script prints surrounding context |
| `EMPTY_MODEL` | Output contains "Current top level object is empty" | Module not called, bad `difference()`, or zero parameters |
| `HEADLESS_PREVIEW` | Output contains OpenGL-related errors | Normal on headless servers; STL export still works |
| `WARNING` | Output contains "warning" (case-insensitive) | Review the listed warnings; usually non-fatal |
| `OK` | None of the above | No issues detected |

After categorization, also prints:
- `echo()` output from the model (variable values, computed dimensions)
- STL geometry stats if a valid STL was produced

---

### `openscad-stl-analyze.sh`

**Location:** `scripts/openscad-stl-analyze.sh`  
**Purpose:** Raw binary STL inspection without requiring OpenSCAD. All analysis done via Python's `struct` module.

Has three operation modes selected by flags:

---

#### Default mode (no flags): Full mesh analysis

**CLI:** `openscad-stl-analyze.sh <file.stl>`

Parses the binary STL, collects all unique vertices, and reports:

- Triangle count and unique vertex count
- X/Y/Z bounding box (min, max, span in mm)
- Center of bounding box
- Symmetry check (whether the model is centered on each axis)
- Count of distinct values per axis
- **Gap detection:** finds spans along each axis greater than 5% of the total range; these indicate internal feature boundaries (e.g., a slot or pocket)

---

#### Cross-section mode: `--cross-section <axis> <value>`

**CLI:** `openscad-stl-analyze.sh model.stl --cross-section z 5.0`

Finds all vertices within a small tolerance (`0.02 mm`, widened to `0.2 mm` if nothing found) of a given plane. Reports the 2D coordinate ranges at that cross-section and renders a text-art bar chart of absolute-value distributions to reveal internal features.

---

#### Gap mode: `--gaps <axis>`

**CLI:** `openscad-stl-analyze.sh model.stl --gaps y`

For each distinct level along the target axis, computes absolute-value distributions on the other two axes and reports gaps wider than `0.5 mm`. These gaps are feature boundaries (transitions between a wall's outer and inner face, hole edges, etc.).

---

### `openscad-stl-reconstruct.sh`

**Location:** `scripts/openscad-stl-reconstruct.sh`  
**Purpose:** Full automated reconstruction analysis pipeline. Converts an STL into raw analysis data that Claude then uses to write parametric `.scad` code.

**Usage:** `openscad-stl-reconstruct.sh <file.stl> <output_dir>`

Runs four steps in sequence:

---

#### Step 1: Mesh analysis (Python / trimesh)

Loads the STL with `trimesh`. Reports:

- Vertex and face count
- Volume (mm³)
- Watertight status
- Bounding box and dimensions
- Primary axis (longest dimension → likely extrusion direction)
- Bounding cylinder (height and radius via `trimesh.bounds.minimum_cylinder`)
- Face normal alignment: counts faces aligned with each cardinal axis
- Curved/non-planar face percentage (faces whose normals don't align with any axis)

Saves results to `<output_dir>/mesh-info.json`.

---

#### Step 2: 2D Profile Slices (OpenSCAD projection)

Reads the Z bounds from `mesh-info.json`. Creates 5 temporary `.scad` files — one at each of 1%, 25%, 50%, 75%, and 99% of the Z height — using this pattern:

```openscad
projection(cut=true)
    translate([0, 0, -<Z_LEVEL>])
        import("<STL_FILE>", convexity=10);
```

Exports each as an SVG to `<output_dir>/slices/`. Reports file size; slices smaller than 200 bytes are considered empty and discarded. The resulting SVGs show the 2D cross-section profile of the mesh at that height.

---

#### Step 3: Primitive Detection (Python / trimesh + RANSAC)

Parses face normals to identify planar and cylindrical primitives:

- **Planar facets:** Groups large contiguous flat regions (`trimesh.facets`). Facets with area > 10 mm² are recorded with their average normal, centroid, and area.
- **Cylindrical surfaces:** Identifies curved faces (normals not aligned with any axis). Runs a simple RANSAC by cross-multiplying random pairs of curved normals to estimate the cylinder's rotation axis. Projects curved vertices onto 2D to measure the radius. Determines if the cylinder is **inward-facing** (a hole → `difference()`) or **outward-facing** (a boss/tube → `union()`).

Saves all detected primitives to `<output_dir>/primitives.json`.

---

#### Step 4: Report Summary

Lists generated SVG slices and JSON files. Prints paths for manual inspection.

---

### `openscad-stl-compare.sh`

**Location:** `scripts/openscad-stl-compare.sh`  
**Purpose:** Geometric comparison of two STL files (original vs. reconstruction). Measures accuracy quantitatively.

**Usage:** `openscad-stl-compare.sh <original.stl> <reconstruction.stl> [output_dir]`

---

#### Step 1: Dimensional Comparison (Python / struct)

Parses both binary STL files to extract bounding boxes. Prints a table:
- Triangle count for each
- X/Y/Z dimension (span) for each
- Delta per axis
- Center offset per axis (flags misalignment)
- Total dimensional delta

---

#### Step 2: Boolean Difference Renders

Creates three temporary `.scad` files:

- **A−B:** `difference() { import(A); import(B); }` — geometry in original but missing from reconstruction
- **B−A:** `difference() { import(B); import(A); }` — extra geometry added by reconstruction
- **Overlay:** `%import(A); color("red", 0.5) import(B);` — transparent overlay for visual comparison

Renders each as an 800×600 PNG using the `DeepOcean` color scheme. Also exports the boolean difference geometries as temporary STL files for volume analysis.

---

#### Step 3: Volume Analysis (Python / struct)

Computes the signed volume of each STL and the two difference STLs using the **signed tetrahedron method** (sum of `(v1 · (v2 × v3)) / 6` across all triangles).

Reports:
- Original volume (mm³)
- Reconstruction volume (mm³)
- Volume delta and delta percentage
- Volume of A−B (missing geometry) in mm³
- Volume of B−A (extra geometry) in mm³
- **Geometric accuracy %** = `(1 - (vol_AB + vol_BA) / vol_A) * 100`
- Qualitative result: Excellent (>99%), Good (>95%), Fair (>90%), or Needs Refinement

---

## Python Scripts

### `openscad-sdf-optimize.py`

**Location:** `scripts/openscad-sdf-optimize.py`  
**Purpose:** Finds optimal numerical parameters for an OpenSCAD reconstruction model using Signed Distance Fields (SDF) and IoU scoring — without ever invoking OpenSCAD in the optimization loop.

---

#### SDF primitive functions

Each function takes an array of 3D sample points `p (N×3)` and returns a signed distance for each point (negative = inside, positive = outside).

| Function | Signature | Description |
|---|---|---|
| `sdf_box` | `(p, size)` | Axis-aligned box centered at origin |
| `sdf_cylinder_x` | `(p, radius, half_length, center=None)` | Finite cylinder along X axis |
| `sdf_capsule_x` | `(p, radius, half_span, center=None)` | Capsule (cylinder with hemispherical caps) along X |
| `sdf_stadium_extrude` | `(p, total_len, width, height)` | Stadium profile (rectangle with rounded ends) extruded along Z |

---

#### CSG operation functions

| Function | Description |
|---|---|
| `sdf_union(d1, d2)` | Boolean union: `min(d1, d2)` |
| `sdf_difference(d1, d2)` | Boolean difference: `max(d1, -d2)` |
| `sdf_intersection(d1, d2)` | Boolean intersection: `max(d1, d2)` |

---

#### Scoring function: `iou_score(sdf_vals, target_inside)`

Computes the Intersection over Union between two boolean masks (what the parametric SDF thinks is "inside" vs. what the original mesh samples say is "inside"). Returns a value from 0 (no overlap) to 1 (perfect match). The optimizer maximizes this.

---

#### Model registry: `MODEL_REGISTRY`

A dictionary of predefined model types. Each entry specifies:
- `param_names` — list of parameter names to optimize
- `bounds` — (min, max) for each parameter
- `initial` — starting guess for each parameter
- `sdf_fn` — a function that takes `(points, params)` and returns signed distances

Currently registered models:
- **`stadium-slot`** — a stadium-profile extrusion with a cylindrical slot cut through it (the toothpaste squeezer shape)
- **`box-holes`** — a rectangular box with through-holes

---

#### `optimize(stl_file, model_type, n_samples, verbose)`

Main optimization entry point:

1. Loads the STL with `trimesh`
2. Samples `n_samples` random points (half inside the mesh, half outside) using `trimesh.sample.volume_mesh` and surface sampling
3. Looks up the model type in `MODEL_REGISTRY`
4. Runs `scipy.optimize.minimize` (L-BFGS-B) on the negative IoU score
5. Returns a dict with: `model_type`, `params`, `iou_score`, and generated OpenSCAD code

---

#### `generate_scad(model_type, param_names, params)`

Takes optimized parameter values and renders them into a ready-to-use OpenSCAD code string. Currently implements a code generator for `stadium-slot`.

---

### `openscad-adaptive-slice.py`

**Location:** `scripts/openscad-adaptive-slice.py`  
**Purpose:** Multi-axis adaptive SVG slicing with coarse-then-fine resolution. More thorough than the fixed-5-level slicing in `openscad-stl-reconstruct.sh`.

---

#### `segments_to_polygons(segments, snap=1e-4)`

Converts raw mesh cross-section line segments into closed Shapely polygons. Snaps coordinates to a grid (default 0.1 µm) to merge nearly-identical endpoints. Uses `shapely.ops.polygonize` to close loops.

---

#### `slice_at_height(mesh, axis, height)`

Slices the mesh at a given height along the specified axis (0=X, 1=Y, 2=Z) using `trimesh.intersections.mesh_multiplane`. Converts the resulting segments to polygons, computes combined area, hole count, and perimeter via Shapely. Returns a descriptor dict or `None` if the slice is empty.

---

#### `adaptive_slice_axis(mesh, axis, coarse_step, fine_step)`

Two-pass adaptive slicer for one axis:

1. **Coarse pass** — slices every `coarse_step` mm (default 5 mm). Records area at each level.
2. **Transition detection** — finds heights where the area changes by more than 10% between adjacent slices. These are feature boundaries.
3. **Fine pass** — re-slices with `fine_step` mm resolution (default 0.5 mm) only around detected transitions, within a ±`coarse_step` window.
4. Returns a merged, deduplicated list of slice descriptors sorted by height.

---

#### `extract_features(slices)`

Post-processes slice descriptors to tag semantic feature events:
- `area_change` — large cross-section area change (feature added/removed)
- `hole_added` / `hole_removed` — interior hole count change
- `topology_change` — polygon count change (model splits or merges)

---

#### `main()`

CLI entry point. Parses `--coarse` and `--fine` step size arguments. Runs `adaptive_slice_axis` for all three axes. Saves results as JSON. Prints a feature map summary to stdout.

---

### `openscad-profile-extract.py`

**Location:** `scripts/openscad-profile-extract.py`  
**Purpose:** Detects the extrusion axis of a mesh and extracts the dominant cross-section profile as an OpenSCAD `polygon()` + `linear_extrude()` code block.

---

#### `segments_to_polygons(segments, snap=1e-4)`

Same segment-to-polygon conversion as in `openscad-adaptive-slice.py` using Shapely.

---

#### `detect_extrusion_axis(mesh, n_slices=64)`

Finds which axis (X, Y, or Z) the model is most consistently extruded along. Strategy:

1. Aligns the mesh to its Oriented Bounding Box (OBB) for canonical orientation.
2. For each axis, takes `n_slices` evenly spaced cross-sections.
3. Computes the variance of cross-section area across slices.
4. The axis with the **lowest area variance** is the extrusion direction (the profile stays most consistent along that axis).

Returns a tuple of: `(stability_score, axis_index, obb_transform, heights, polygon_list)`.

---

#### `extract_dominant_profile(mesh, axis, transform, heights, polygons)`

Given the detected extrusion axis, finds the slice with the largest area (the "dominant profile"). Applies the OBB inverse transform to get coordinates back in original mesh space. Returns the polygon's exterior coordinates and any interior hole coordinates.

---

#### `simplify_polygon(coords, tolerance)`

Applies the Ramer-Douglas-Peucker algorithm (via Shapely's `simplify`) to reduce the number of polygon vertices while preserving shape. Returns simplified coordinate list.

---

#### `generate_scad(coords, holes, axis, height, tolerance)`

Converts the extracted 2D profile into a complete OpenSCAD code string:

```openscad
linear_extrude(height = <height>)
    difference() {
        polygon(points = [...]);     // outer profile
        polygon(points = [...]);     // hole 1 (if any)
    }
```

Rotates the extrude direction to match the detected axis (X or Y extrusion uses a `rotate()` wrapper).

---

#### `main()`

CLI entry point. Parses `--output` (output `.scad` file) and `--simplify` (Douglas-Peucker tolerance) flags. Runs the full pipeline and either prints or saves the generated OpenSCAD code.

---

## Templates

### `enclosure.scad`

**Location:** `templates/enclosure.scad`

A fully parametric electronics enclosure with a separate lid. Key parameters:

- `width`, `depth`, `height` — outer dimensions
- `wall` — wall thickness
- `corner_r` — corner fillet radius
- `lid_h` — lid height
- `tolerance` — clearance for lid fit

Uses the Feature Tree pattern: rounded base profile via `offset(r=corner_r)`, `linear_extrude`, then `difference()` for the hollow interior and screw holes.

---

### `bracket.scad`

**Location:** `templates/bracket.scad`

A parametric L-bracket with countersunk mounting holes. Key parameters:

- `width`, `leg_h`, `leg_d` — overall shape
- `thickness` — bracket material thickness
- `hole_d`, `cs_d`, `cs_h` — countersink screw hole dimensions

Demonstrates the sculptor approach: solid L-body first, then `difference()` subtracts all holes at once.

---

### `printable-lib.scad`

**Location:** `templates/printable-lib.scad`

A reusable module library for common 3D-printing patterns. All modules are designed to be `use <printable-lib.scad>`'d in other files.

| Module | Signature | Description |
|---|---|---|
| `shell_box` | `(outer, wall, floor)` | Hollow box with configurable wall and floor thickness |
| `rounded_box` | `([x,y,z], r)` | Box with rounded vertical edges via `offset(r)` |
| `screw_clearance_hole` | `(d, h, fit)` | Through-hole with fit clearance applied |
| `counterbore_hole` | `(shaft_d, head_d, head_h, h)` | Counterbore (cylindrical recess, flat bottom) |
| `countersink_hole` | `(d, cs_d, cs_h, h)` | Countersink (conical recess, angled sides) |
| `heatset_boss` | `(insert_d, insert_h, wall, h)` | Hollow cylinder boss for heat-set inserts |
| `screw_post` | `(outer_d, inner_d, h)` | Solid screw post for self-tapping screws |
| `rib` | `(len, height, thick)` | Vertical stiffening rib |
| `snap_tab` | `(width, length, thick, overhang)` | Snap-fit cantilever tab |
| `text_label` | `(text, size, depth)` | Debossed text label |

Helper function:

| Function | Returns |
|---|---|
| `fit_clearance("press")` | `0.15` mm |
| `fit_clearance("close")` | `0.25` mm |
| `fit_clearance("slide")` | `0.30` mm |
| `fit_clearance("loose")` | `0.40` mm |

---

## References

### `language-reference.md`

**Location:** `references/language-reference.md`

A concise cheat sheet of OpenSCAD v2021.01 syntax. Claude reads this when generating `.scad` code. Covers:

- Primitive shapes: `cube`, `sphere`, `cylinder`, `polyhedron`
- 2D shapes: `circle`, `square`, `polygon`, `text`
- Boolean operations: `union`, `difference`, `intersection`
- Transforms: `translate`, `rotate`, `scale`, `mirror`, `multmatrix`
- Extrusions: `linear_extrude`, `rotate_extrude`
- Modifiers: `%` (transparent), `#` (highlight), `!` (only), `*` (disable)
- Special variables: `$fn`, `$fa`, `$fs`, `$t`, `$children`
- Control flow: `for`, `each`, `if/else`, `let`
- Functions and modules
- Common pitfalls (epsilon, coplanar faces, `hull()` vs `minkowski()`)

---

### `reconstruction-guide.md`

**Location:** `references/reconstruction-guide.md`

Best practices guide for the STL-to-SCAD reconstruction workflow. Claude reads this before starting any Reconstruct mode task. Covers:

- The sculptor approach: always start with a solid body, subtract everything last
- How to read SVG profile slices and interpret feature boundaries
- How to distinguish holes from bosses using normal analysis
- Epsilon usage to prevent coplanar Boolean artefacts
- Common reconstruction mistakes and how to avoid them
- When to use SDF optimization vs. manual measurement

---

## Skill Definition

### `SKILL.md`

**Location:** `SKILL.md` (762 lines)

The core instruction document that Claude Code reads when the skill is triggered. Structured as a YAML front-matter block followed by a Markdown body.

**Front matter fields:**

| Field | Value |
|---|---|
| `name` | `openscad` |
| `description` | Trigger keywords and capability summary |
| `argument-hint` | `"<description of object or path to .scad file>"` |
| `allowed-tools` | `Bash(*), Read, Edit, Write, Glob, Grep, Agent` |
| `version` | `1.0.0` |
| `category` | `3d-cad` |

**Body sections:**

- **Environment** — where scripts, templates, and projects live
- **Modes** — the six operating modes and how to auto-detect them
- **Workflow: Design Mode** — step-by-step: init project → write `.scad` → render → review PNG → iterate
- **Workflow: Replicate Mode** — image measurement → parameter extraction → design
- **Workflow: Reconstruct Mode** — full pipeline from STL analysis through mesh comparison
- **Workflow: Refine Mode** — read existing file → apply changes → re-render
- **Workflow: Export Mode** — parameter overrides → STL/3MF export
- **Workflow: Analyze Mode** — validate → bottom/wireframe render → report
- **OpenSCAD Code Standards** — mandatory patterns: Feature Tree structure, sculptor approach, epsilon, `$fn`, `assert()`, profile-first design
- **3D Printing Guidelines** — wall thickness, clearances, overhang angles, flat bottoms
- **Script Reference** — quick lookup of all script commands
- **Common Patterns** — annotated code examples for enclosures, brackets, snap-fits, text labels

---

## Eval Framework

### `eval/eval.json`

Defines 4 test scenarios with a total of 20 binary assertions.

| Scenario ID | Assertions | What it validates |
|---|---|---|
| `design-simple-box` | 6 | Project init, parametric vars at top, module usage, `$fn` setting, rendering, valid syntax |
| `design-sculptor` | 5 | Sculptor approach, epsilon variable, comments, printability, visual preview |
| `reconstruct-stl` | 5 | Analysis-before-code, SVG profiling, sculptor approach, mesh comparison, data-driven dimensions |
| `export-parametric` | 4 | Use of render script, `-D` flags, output path reporting, file size reporting |

Each assertion is a binary yes/no question phrased so Claude Code can evaluate its own output objectively.

---

### `eval/results.jsonl`

Append-only log of test run results in JSONL format. Each line is a JSON object with the scenario ID, assertion IDs, pass/fail for each, timestamp, and notes. The baseline on file shows 20/20 assertions passing.
