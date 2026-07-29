# Blender PCB Generator — Build Plan

Status: **draft for review**. Nothing here is locked in; the open decisions are
collected in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) and should be settled before
Phase 1 code lands.

---

## 1. What we're building

A Blender extension that lets you:

1. **Draw a board shape** — any 2D outline (rectangle, rounded, organic, with
   internal cutouts), not just the rectangle every EDA tool defaults to.
2. **Populate it with parts** — connectors, ports, headers, buttons, mounting
   holes, ICs — pulled from a component library.
3. **Place them modularly** — parts carry placement *rules* (edge-anchored,
   grid-snapped, keep-out-aware) rather than frozen coordinates, so when the
   outline changes the population re-solves instead of breaking.
4. **Get something out** — a mechanically accurate 3D board for enclosure work
   and renders, plus a board outline + placement file that an EDA tool can
   consume.

The bet: Blender is already the best tool for *shape*, and PCB tools are the
worst at it. This inverts the normal workflow — mechanical intent first,
electrical layout second.

### Primary use case (assumed)

Industrial-design-driven boards: you know the enclosure, the port positions, and
the human-facing controls before you know the netlist. You want the outline and
connector placement to be *the* source of truth, exported downstream to KiCad.

If the real target is something else (pure render/visualization, or a full EDA
replacement), several decisions below flip — see Open Questions Q1.

---

## 2. Non-goals

Worth stating early so scope doesn't creep:

- **Not a router.** No traces, no netlist, no autorouting, no copper pours.
- **Not a simulator.** No electrical checks, no signal integrity.
- **Not a Gerber writer.** Fabrication output belongs to the EDA tool.
- **Not a replacement for KiCad/Altium.** It's an upstream stage that hands off.

What it *does* own: outline geometry, mechanical constraints, part placement,
clearance/keep-out checks, and enclosure interface geometry.

---

## 3. Target platform

| Item | Choice | Rationale |
|---|---|---|
| Blender | 4.2 LTS minimum, tested on 5.2 LTS | 4.2 is the first extension-platform release; going older means maintaining `bl_info` and a second install path |
| Packaging | Extension (`blender_manifest.toml`) | Required for the extensions platform; enables bundled wheels and declared permissions |
| Python | Whatever ships with the target Blender | No external interpreter |
| Dependencies | Ideally zero; bundled wheels if needed | Every wheel is an install-failure mode. `shapely` is the one candidate worth it (see §5.2) |
| Scene units | Millimeters, unit scale 0.001, 1 BU = 1 mm | Non-negotiable for PCB work. The addon sets this up and warns loudly if the scene disagrees |

---

## 4. Architecture

```
pcb_generator/
├── blender_manifest.toml
├── __init__.py                 # registration only, no logic
├── core/                       # pure Python, no bpy imports — unit testable
│   ├── geometry.py             # polygon ops, offsets, edge walking
│   ├── placement.py            # the constraint solver
│   ├── drc.py                  # clearance + overlap rules
│   └── units.py                # mm <-> BU, pitch snapping
├── data/
│   ├── board.py                # BoardProperties PropertyGroup
│   ├── component.py            # ComponentProperties PropertyGroup
│   └── library.py              # part manifest loading/indexing
├── ops/                        # operators (thin — call into core/)
│   ├── board_ops.py
│   ├── place_ops.py            # includes the interactive modal placer
│   └── export_ops.py
├── ui/
│   ├── panels.py               # N-panel, tabbed
│   ├── gizmos.py               # edge anchors, courtyard handles
│   └── overlays.py             # GPU-drawn keep-out / violation display
├── io/
│   ├── dxf.py  svg.py  kicad.py  csv_pos.py
├── assets/
│   └── parts.blend             # seed component library
└── tests/                      # pytest against core/, headless bpy for the rest
```

**The one architectural rule:** `core/` never imports `bpy`. Geometry and
solving are plain Python over plain data. This is what makes the project
testable in CI without a Blender binary, and it's the difference between a
maintainable addon and the usual 3000-line `__init__.py`.

---

## 5. Core subsystems

### 5.1 Board outline

Input paths, in priority order:

1. **Curve object** (Bézier/poly) — primary. User draws it, we read control
   points. Supports fillets natively.
2. **Grease Pencil stroke** — fastest sketching path; convert to curve, then
   simplify/fit.
3. **Mesh face** — for users who'd rather box-model. Take the boundary loop.

Pipeline: source → **closed planar polygon** (outer ring + N inner rings for
cutouts) → validate (self-intersection, min feature size, closure) → solidify to
board thickness (default 1.6 mm) → tag faces top/bottom/edge for materials.

Board geometry is **regenerated, never hand-edited**. The curve is the source of
truth; the mesh is derived output. This is what lets parts re-solve when the
shape changes.

Validation rules from the start: minimum internal radius (router bit ≥ 0.8 mm
typical), minimum slot width, self-intersection, and non-planar control points.
These are cheap to check and catch the errors that only surface at the fab
house.

### 5.2 Geometry backend decision

Polygon offsetting (for courtyard inflation, edge clearance bands, keep-out
zones) is the hard part. Three options:

- **`shapely` bundled as a wheel** — robust, battle-tested offsetting and
  boolean ops. Costs an install-time dependency and ~5 MB.
- **Hand-rolled** — straight skeleton / miter offset. No dependency, but
  offsetting concave polygons correctly is genuinely hard and a well-known
  source of subtle bugs.
- **Blender's own tools** (Solidify, Boolean, curve offset) — free, but the
  results are mesh-domain and awkward to query for DRC.

**Recommendation: bundle `shapely`.** The correctness risk of hand-rolled
offsetting outweighs the packaging cost, and `core/` staying bpy-free means
`shapely` slots in cleanly. Flagged as Q4 — reversible if we'd rather ship
dependency-free.

### 5.3 Component data model

A part is a **Blender collection** with custom properties, instanced per
placement. Properties on each instance:

| Property | Meaning |
|---|---|
| `ref` | Designator (`J1`, `SW2`) — auto-numbered per prefix |
| `part_id` | Library key |
| `side` | `TOP` / `BOTTOM` (bottom mirrors and flips Z) |
| `courtyard` | 2D polygon, mm — the real footprint including clearance |
| `keepout_3d` | Optional volume — for tall parts under a lid |
| `anchor` | `FREE` / `EDGE` / `GRID` / `RELATIVE` — see §5.4 |
| `anchor_data` | Edge index + parameter along edge, or grid cell, or parent ref |
| `pin1` | Orientation reference so rotation means something |
| `panel_face` | Whether this part must protrude through the enclosure |
| `cutout_solid` | Optional mesh for enclosure boolean subtraction |

Library entries are **JSON manifests + a `.blend` per part family**, indexed
into Blender's Asset Browser with catalogs (Connectors / Ports / Headers /
Controls / Mechanical / Packages). Users add parts by dropping a folder in —
no code changes.

Seed library (Phase 2 deliverable):

- **Ports:** USB-C (recept. + through-hole variants), USB-A, micro-B, HDMI,
  DisplayPort, RJ45, 3.5 mm TRS, barrel jack, microSD
- **Headers:** 2.54 mm 1×N and 2×N (N = 2…40), 2.00 mm, 1.27 mm, JST PH/XH/SH
- **Controls:** tactile switches (6 mm, 12 mm), slide/DIP switches, rotary
  encoders, potentiometers, LEDs (3 mm/5 mm/SMD), 7-seg
- **Mechanical:** M2/M2.5/M3 mounting holes with keep-out annuli, standoffs,
  card-edge fingers
- **Packages:** DIP-8…40, SOIC, QFN, QFP, TO-220, common SMD passives

### 5.4 Placement / constraint solver

The differentiating feature. Each part declares an anchor; on board change the
solver re-derives world transforms.

- **`EDGE`** — anchored to edge *i* at normalized parameter *t*, with an
  outward offset (protrusion/inset). Auto-oriented to the edge normal. When the
  outline changes, edges are re-matched by identity (stable IDs assigned at
  outline build, not by index) and *t* is preserved. **Edge identity tracking is
  the single hardest correctness problem in this project** — if it's wrong,
  connectors teleport on every outline tweak. Design for it up front.
- **`GRID`** — snapped to a pitch grid (2.54 / 2.00 / 1.27 / 0.5 mm),
  optionally board-relative or region-relative.
- **`RELATIVE`** — offset from another part's frame. For header banks, paired
  connectors, LED arrays.
- **`FREE`** — absolute coordinates, no re-solve.

Solve order: FREE → EDGE → GRID → RELATIVE (dependency-sorted, cycles rejected
with a clear error). Solving is **explicit** (a "Re-solve" operator) plus an
opt-in depsgraph handler — automatic-everything on a depsgraph update is how
addons become unusably slow on real scenes.

Conflict handling: the solver never silently moves a part to fix an overlap. It
places per the rules and reports violations to DRC. Auto-resolution is a
Phase 5+ idea, not a v1 behavior.

### 5.5 Interactive placement

A modal operator: pick a part from the library, move the mouse, and it snaps to
the nearest legal anchor with live preview.

- Hovering near an edge → edge anchor with outward orientation
- Over open board → grid snap
- Near an existing part → relative-anchor offer
- `Tab` cycles anchor mode, `R` rotates in 90° steps (fine with modifier),
  `F` flips side, `Esc` cancels
- Live courtyard overlay, red when it violates something

### 5.6 Checks (DRC-lite)

Not electrical DRC — mechanical only:

- Courtyard overlaps
- Part extending past the outline (unless flagged as intentional overhang)
- Edge clearance (default 0.5 mm component-to-edge)
- Mounting hole keep-out violations
- Connector accessibility: does a `panel_face` part's opening actually face
  outward through the outline, unobstructed?
- Height violations against an optional lid plane
- Duplicate reference designators

Results render as a GPU overlay (colored courtyards) plus a list panel with
click-to-select. Every violation names the rule and the parts involved.

### 5.7 Export

| Format | Contents | Priority |
|---|---|---|
| DXF | Board outline + cutouts, mm, closed polylines → KiCad `Edge.Cuts` | P0 |
| SVG | Same, for documentation and laser templates | P0 |
| CSV | Placement: `ref, part_id, x, y, rotation, side` — KiCad pos-file compatible | P0 |
| `.kicad_pcb` | Outline + footprint placement in one file | P1 |
| STL / glTF | 3D board for enclosure CAD and viz | P1 |
| Cutout solids | Boolean bodies per `panel_face` part, for subtracting from an enclosure | P1 — **this is the sleeper feature** |

Export coordinates: board origin at a user-set datum (default outline
bounding-box min corner), Y-up-positive, mm, so KiCad import lands where
expected without manual nudging.

---

## 6. Phasing

Each phase ends with something usable, not a half-built layer.

**Phase 0 — Scaffold (small)**
Extension manifest, registration, empty N-panel, scene unit setup + warning,
CI running `pytest` on `core/`, headless-Blender smoke test. Establishes the
bpy-free boundary before there's anything to untangle.

**Phase 1 — Board (medium)**
Curve → validated polygon → solidified board mesh. Cutouts, thickness, fillets,
stable edge IDs, top/bottom/edge material slots. *Usable: you can draw a board
shape and get real geometry.*

**Phase 2 — Library + manual placement (large)**
Component data model, JSON manifests, asset library, seed parts, interactive
modal placer with snapping, ref auto-numbering. *Usable: draw a board, populate
it, render it.*

**Phase 3 — Constraint solver (medium, highest risk)**
Anchor types, dependency-sorted re-solve, edge identity preservation across
outline edits. *Usable: change the shape, the population follows.*

**Phase 4 — Checks (medium)**
DRC-lite rules, GPU overlay, violation panel.

**Phase 5 — Export (medium)**
DXF/SVG/CSV first, then `.kicad_pcb` and cutout solids.

**Phase 6 — Polish**
Board render presets (soldermask/silkscreen/ENIG materials), documentation,
example projects, extension-platform submission.

Phase 3 is the schedule risk. If edge identity proves harder than expected,
Phase 2's output still stands on its own as a useful tool — which is why the
phasing is ordered this way.

---

## 7. Testing

- `core/` — pure pytest, no Blender. Polygon ops, solver, DRC rules. Should be
  the bulk of the test suite.
- Operators — headless `blender --background --python` running a scripted
  scenario, asserting on scene state.
- Golden files — a few reference boards exported to DXF/CSV, diffed.
- CI — matrix over Blender 4.2 LTS and 5.2 LTS.

The bpy-free `core/` boundary exists precisely so most logic is testable in
milliseconds rather than through a 20-second Blender launch.

---

## 8. Known risks

| Risk | Mitigation |
|---|---|
| Edge identity across outline edits (§5.4) | Prototype this *first* in Phase 1 with stable IDs, before anything depends on it |
| Concave polygon offsetting correctness | Bundle `shapely` rather than hand-rolling |
| Library scope creep — parts are endless | Ship a small seed set; make user-authored parts a first-class, documented path |
| Solver perf on large boards | Explicit re-solve by default; dirty-flag incremental solving if needed |
| Users' scenes not in mm | Detect and offer a one-click fix on addon activation |
| KiCad round-trip fidelity | Treat one-way export as the contract; import is a separate, later question |

---

## Sources

- [How to Create Extensions — Blender 5.2 LTS Manual](https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html)
- [Add-on Development Setup — Blender Developer Documentation](https://developer.blender.org/docs/handbook/extensions/addon_dev_setup/)
- [blender_manifest.toml template](https://fossies.org/linux/blender/scripts/templates_toml/blender_manifest.toml)
