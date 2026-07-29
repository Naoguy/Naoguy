# Blender PCB Generator — Build Plan

Status: **draft, revised for the simplified brief.** Open decisions in
[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).

---

## 1. What we're building

Give it a shape. Get back a believable PCB.

That's the whole tool. You've already decided where the board goes and what it
looks like — the generator's job is to turn that shape into something that reads
as a real circuit board in a render.

**The loop:**

```
shape  →  board  →  materials  →  procedural detail  →  scattered parts  →  placed parts
                                  traces, silkscreen,    random fill        ports, buttons —
                                  pads, vias                                exact control
```

Everything below serves that pipeline. Nothing analyzes cavities, avoids
obstacles, or decides where your board should be — that's your call, already
made.

---

## 2. Non-goals

- **No fabrication output.** No Gerbers, DXF, KiCad, placement files.
- **No electrical correctness.** Traces are decoration following plausible rules.
- **No shape authoring assistance.** No fit-to-cavity, no obstacle avoidance, no
  volume analysis. You bring the shape.
- **Not the enclosure.** Shells and button caps are modeled elsewhere; the
  generator can *reference* them for placement but never generates them.

---

## 3. Target platform

| Item | Choice |
|---|---|
| Blender | 4.2 LTS minimum, developed on 5.2 LTS |
| Packaging | Extension (`blender_manifest.toml`) |
| Dependencies | Zero |
| Units | Millimeters, unit scale 0.001 — addon sets this up and warns if the scene disagrees |

---

## 4. Architecture

```
pcb_generator/
├── blender_manifest.toml
├── __init__.py                 # registration only
├── core/                       # no bpy imports — unit testable
│   ├── geometry.py             # polygon ops, boundary extraction
│   ├── routing.py              # trace generator
│   ├── scatter.py              # part distribution
│   └── placement.py            # anchors for precise parts
├── data/                       # PropertyGroups, library manifests
├── ops/
│   ├── board_ops.py            # the three shape inputs
│   ├── detail_ops.py           # traces, silkscreen, pads, vias
│   ├── scatter_ops.py
│   ├── place_ops.py            # modal placer for notable parts
│   └── wire_ops.py
├── ui/                         # panels, gizmos, overlays
├── shading/                    # stack-up material builder, presets
├── assets/parts.blend
└── tests/
```

`core/` never imports `bpy` — geometry, routing, and scatter are plain Python
over plain data, testable without launching Blender.

---

## 5. Subsystems

### 5.1 Shape input — three paths

All three converge on the same internal representation: **a board surface plus
its boundary**. Everything downstream is identical regardless of how you got
there.

**(a) Draw it.** Bézier/poly curve → closed polygon → solidify to thickness
(default 1.6 mm).

**(b) Extract curves from an object.** Pull a boundary from existing geometry:
the open boundary loop of a flat mesh, a planar cross-section through a solid, a
silhouette from a view direction, or a selected edge loop. Result is a curve you
can then edit before building — extraction gives you a starting outline, not a
locked one.

**(c) Point at an object that already is the board.** You have a flat mesh
that's the right shape — make it a PCB in place. Tag its faces top/bottom/edge,
apply the stack-up, generate detail across its surface. No rebuilding, no
re-topology; the object's own geometry is the board.

Path (c) has an architectural consequence worth stating up front: **detail
generation must work on an arbitrary given surface**, not just on outlines the
addon generated itself. So the trace generator operates in the board's UV/surface
space and never assumes a shape it produced. That constraint is cheap if it's
designed in from the start and painful to retrofit — so it's designed in.

Boards can be non-flat under (c) — a gently curved board still works, since
routing is surface-parametric.

### 5.2 Materials / stack-up

A proper layered board shader, not a green diffuse:

FR4 substrate → copper → soldermask → silkscreen → surface finish.

Soldermask in green / black / blue / red / white / purple, matte or gloss;
finish in ENIG gold or HASL silver. Driven by the layer masks §5.3 generates, so
mask correctly opens over pads and silkscreen sits above it. Presets for the
common looks, every parameter exposed.

Lands early — a plain board with good materials already reads better than a
detailed one with bad materials.

### 5.3 Procedural detail

The single biggest believability win, and where most of the work is.

**Traces.** A deliberately fake router: 45°/90° segments on a coarse grid, bus
runs following the board's long axis, fan-out from IC packages, via sprinkling,
ground pour on the reverse. Correctness is irrelevant; *statistics* are
everything — density, bundle parallelism, and consistent width are what the eye
reads. Fully seeded, so the same seed gives the same board and you can hunt for
a layout you like by changing one integer.

Contextual mode: traces preferentially route *between* placed components rather
than ignoring them, and fan out from their pads. Cheap to implement once parts
exist, and it's the difference between "traces on a board" and "traces that
belong to this board."

**Also generated:** pads with proper finish, vias, silkscreen part outlines and
reference designators, fiducials, board-edge markings, and text blocks.

**Output modes.** Texture by default — traces rasterized into the copper and
mask layers, cheap at any normal render distance. Geometry realizer for
close-ups, sharing the same routing data, so it's the same board either way.

### 5.4 Scattered parts

Fill the board with plausible small hardware: SMD passives (0402/0603/0805),
small ICs, crystals, inductors, LEDs, test points.

Seeded and density-driven, with enough rules to avoid looking like confetti:
grid-ish alignment rather than free rotation, clustering into functional-looking
groups, respecting margins from the board edge, and keeping clear of precisely
placed parts and keep-out regions. Paintable density if a uniform spread isn't
what you want.

This is decoration and treats itself as such — fast to regenerate, easy to
reroll, no bookkeeping.

### 5.5 Precisely placed parts

The other half of the split, and where the controls live. Ports, connectors,
buttons, anything whose position actually matters.

Placement modes:

- **Manual** with snapping — modal operator, live preview, grid/edge snap,
  rotation in 90° steps, flip side.
- **Edge-anchored** — bound to a board edge at a parameter along it, oriented
  outward. Follows the edge if the outline changes.
- **Target-bound** — bound to an external scene object, inheriting XY from it
  and Z from the board plane. This is how a tact switch tracks a plastic button
  cap that's modeled outside the generator: move the cap, the switch follows.

Placed parts are registered as keep-out regions so scatter and routing work
around them automatically.

**Seed library**, weighted to what's actually needed: USB-C / USB-A / micro-B,
tactile switches in several heights, slide switches, FPC/ZIF connectors, JST
PH/SH, headers, mounting holes, plus the generic fill parts §5.4 uses. Parts are
collections with custom properties, indexed via the Asset Browser — adding one
means dropping in a folder, no code.

### 5.6 Wires

Thin point-to-point wire runs — board to driver, board to anything.

Pick a source (a pad, a point on the board, a connector) and a destination (a
point on another object), get a wire: curve-based, adjustable gauge, insulation
material, and adjustable slack so it sags and curves like a real wire instead of
running dead straight. Multiple wires from the same region bundle plausibly
rather than overlapping.

Small subsystem, high payoff — a board with two hand-run wires to a driver reads
as *installed* rather than as a floating asset.

---

## 6. Phasing

**Phase 0 — Scaffold (small).** Manifest, registration, N-panel, unit setup, CI
running `pytest` on `core/`.

**Phase 1 — Board from shape (medium).** All three input paths, thickness,
cutouts, face tagging, plus a basic material so there's something to look at.
*Usable: any shape becomes a board object.*

**Phase 2 — Materials (medium).** Full layered stack-up, soldermask and finish
presets. *Usable: it looks like a real blank board.*

**Phase 3 — Procedural detail (large).** Traces, pads, vias, silkscreen, ground
pour. Texture realizer first. *Usable: it looks like a real populated board from
any normal distance — this is the phase that sells it.*

**Phase 4 — Library + precise placement (large).** Data model, seed parts, modal
placer, edge and target anchors, keep-out registration.

**Phase 5 — Scatter (medium).** Density-driven fill respecting Phase 4's
keep-outs. Contextual routing turned on, so traces relate to real parts.

**Phase 6 — Wires + polish (medium).** Point-to-point wires, LOD, docs, examples.

Phases 1–3 stand alone as a useful tool: shape in, believable board out, no
component work at all. Phase 4 adds the control the brief asks for; 5 and 6
enrich it.

Ordering note: precise placement (4) comes before scatter (5) deliberately —
scatter needs to know what to avoid, and the important parts should claim their
space first.

---

## 7. Testing

- `core/` — pytest, no Blender. Boundary extraction, routing determinism (same
  seed → same layout), scatter distribution properties.
- Operators — headless `blender --background --python` scripted scenarios.
- Visual — reference renders of a fixed scene compared per release, loose
  thresholds. Catches "the shader broke," not sub-pixel drift.
- CI — Blender 4.2 LTS and 5.2 LTS.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Traces look procedurally fake — the failure that undermines the whole tool | Prototype the router early in Phase 3 against reference photos; tune density and bundling, not rule count |
| Boundary extraction fails on messy real-world meshes | Extraction outputs an editable curve, never a locked result; drawn outline is always available as fallback |
| Scatter reads as noise rather than circuitry | Alignment and clustering rules from the start, not uniform random; paintable density as the escape hatch |
| Texture resolution insufficient for close-ups | Geometry realizer shares routing data — same source, different output |
| Detail generation assumes addon-built geometry, breaking path (c) | Surface-parametric routing designed in from the start (§5.1) |
