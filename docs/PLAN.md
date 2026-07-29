# Blender PCB Generator — Build Plan

Status: **draft, revised for visualization-first scope**. Open decisions live in
[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).

---

## 1. What we're building

A Blender extension that generates **believable PCBs for renders** — boards
shaped to fit a specific product cavity, populated with parts that land exactly
where the surrounding hardware needs them.

The problem it solves: library PCB models never fit. Wrong outline, wrong size,
connectors in the wrong place, no relationship to the product they're supposed
to live inside. You end up either modeling a board by hand every time or
accepting one that visibly doesn't belong.

### The driving case

A PCB inside a headset, sitting next to an audio driver, carrying:

- a USB port at a specific shell opening
- tactile switches that must sit directly under external plastic buttons that
  are modeled elsewhere and **not** in this generator's scope
- possibly a wire/cable run off the board
- everything else — traces, silkscreen, passives — as visual texture

Everything in this plan is weighted toward that case being excellent.

### What "believable" means here

Nobody counts your pads in a render. What reads as *wrong* is: a board that
doesn't follow its cavity, connectors that don't line up with shell openings,
buttons that don't sit under their caps, a uniformly green rectangle with no
trace detail, or parts at implausible scale. Those are the targets — not
electrical correctness.

---

## 2. Non-goals

Sharpened now that this is visualization-only:

- **No fabrication output.** No Gerbers, no DXF, no `.kicad_pcb`, no placement
  files. Nothing here is manufacturable and it doesn't need to be.
- **No electrical correctness.** No netlists, no real routing, no DRC. Traces
  are decoration that follows plausible rules.
- **No EDA interoperability.** Not a KiCad front-end.
- **Not the enclosure.** Shell, buttons caps, driver housing are modeled
  elsewhere. This generator *reads* them as context and *matches* them.

Dropping fab output removes roughly a full phase of work from the previous plan
and redirects it into visual detail, which is where the value actually is.

---

## 3. Target platform

| Item | Choice | Rationale |
|---|---|---|
| Blender | 4.2 LTS minimum, developed on 5.2 LTS | 4.2 is the first extension-platform release |
| Packaging | Extension (`blender_manifest.toml`) | Current standard; supports bundled wheels |
| Dependencies | **Zero** | Reversal from the previous plan — see §5.2 |
| Scene units | Millimeters, unit scale 0.001 | Board features are sub-millimeter; the addon sets this up and warns if the scene disagrees |

---

## 4. Architecture

```
pcb_generator/
├── blender_manifest.toml
├── __init__.py                 # registration only
├── core/                       # no bpy imports — unit testable
│   ├── geometry.py             # polygon ops, edge walking, offsets
│   ├── placement.py            # anchor resolution
│   ├── routing.py              # plausible-trace generator
│   └── units.py
├── data/
│   ├── board.py                # BoardProperties
│   ├── component.py            # ComponentProperties
│   └── library.py              # part manifests
├── ops/
│   ├── board_ops.py            # outline build, fit-to-cavity
│   ├── place_ops.py            # modal placer, anchor binding
│   ├── detail_ops.py           # traces, silkscreen, vias, pads
│   └── cable_ops.py
├── ui/
│   ├── panels.py  gizmos.py  overlays.py
├── shading/
│   ├── stackup.py              # material builder
│   └── presets.py              # soldermask colors, finishes
├── assets/
│   └── parts.blend
└── tests/
```

`core/` never imports `bpy`. Geometry, anchor solving, and trace routing are
plain Python over plain data — testable in milliseconds instead of through a
Blender launch.

---

## 5. Core subsystems

Ordered by how much they matter to the headset case.

### 5.1 Board outline — including *derived from context*

Two paths, and the second is the one that matters here.

**Drawn:** Bézier/poly curve → validated closed polygon → solidify to thickness
(default 1.6 mm) → tagged top/bottom/edge faces.

**Derived from context (the important one):** point the addon at a **cavity
object** (headset shell interior) and a set of **obstacle objects** (the audio
driver, mounting bosses, ribs). It computes the available planar region at the
board's placement plane, insets by a clearance value, and produces an outline
that actually fits.

```
cavity ∩ board_plane  →  available region
    minus (obstacles ⊕ clearance)  →  legal region
    minus (keep-out zones)         →  board outline
    → simplify / fillet corners
```

This is the feature that answers "next to an audio driver." Rather than
eyeballing a shape that clears the driver, you declare the driver an obstacle
and get an outline that provably clears it — and re-clears it when the driver
moves.

Both paths converge on the same polygon representation, so everything
downstream is identical. Outline is always **regenerated from source**, never
hand-edited, which is what lets parts re-solve when the shape changes.

### 5.2 Geometry backend — reversed decision

The previous plan recommended bundling `shapely` for robust polygon offsetting,
justified by fabrication-grade correctness.

**That justification is gone.** For visualization, offsetting needs to look
right, not be provably exact — an imperfect miter on a concave corner is
invisible in a render. Combined with the fact that context-derived outlines lean
on Blender's own boolean and solidify tools anyway, bundling a wheel is no
longer worth its install-failure surface.

**Decision: zero dependencies.** Hand-rolled offsetting for the simple cases,
Blender's boolean/curve tools for the rest.

### 5.3 Placement, and anchoring to external geometry

This is the headset requirement stated plainly: *tactile switches must sit under
plastic button caps modeled outside this generator.*

So the anchor system's most important target isn't the board edge — it's
**arbitrary scene objects**. Anchor types:

| Anchor | Binds to | Use |
|---|---|---|
| **`TARGET`** | Any scene object or empty | **Buttons under caps, USB port at shell opening.** The critical one |
| `EDGE` | Board outline edge, param *t* along it | Connectors that just follow the board's border |
| `GRID` | Pitch grid (2.54 / 2.00 / 1.27 mm) | Passives, header banks |
| `RELATIVE` | Another part's frame | Paired parts, LED arrays |
| `FREE` | Absolute | Everything else |

`TARGET` semantics: bind a part to an object, choose which axes it inherits
(usually XY from the target, Z from the board plane), plus an offset. Move the
button cap in the headset model, the tact switch follows. Move the shell, the
USB port follows.

Then it runs the other way too: **`TARGET`-anchored parts can drive the
outline.** If a switch must sit at a given XY and the derived outline doesn't
reach it, the board grows a tab to cover it. That closes the loop between "the
product dictates where things are" and "the board is shaped to suit" — which is
exactly backwards from how EDA tools work, and exactly right here.

Solve order: `FREE` → `TARGET` → `EDGE` → `GRID` → `RELATIVE`, dependency-sorted,
cycles rejected with a clear error. Re-solve is an explicit operator plus an
opt-in depsgraph handler — auto-solving on every depsgraph update is how addons
become unusable in real scenes.

### 5.4 Visual detail generation — promoted to a core phase

Under the old fab-oriented plan this was cosmetic. Now it's most of the
believability, and it's the difference between a generated board and a green
rectangle.

**Traces.** A deliberately fake router. Pick pads, route between them on a
coarse grid with 45°/90° segments, add plausible bus runs along the board,
fan-out from IC packages, sprinkle vias. Correctness is irrelevant; *statistics*
are everything — trace density, bundle parallelism, and consistent width are
what the eye reads. Seeded and deterministic, so a render is reproducible and
you can hunt for a layout you like by changing one integer.

Two output modes:
- **Texture** (default) — traces rasterized into the copper/soldermask layer.
  Cheap, ships fine at any distance a headset interior will ever be seen from.
- **Geometry** — real extruded copper for extreme close-ups. Same routing data,
  different realizer.

**Also generated:** pads and their finish, vias, silkscreen outlines and
reference designators, polygon ground pour on the reverse, fiducials, and
scattered SMD passives to fill empty regions — the last one is a surprisingly
large believability win for near-zero effort.

### 5.5 Material / stack-up system

A proper layered board shader rather than a green diffuse:

FR4 substrate → copper → soldermask (green / black / blue / red / white / purple,
matte or gloss) → silkscreen → surface finish (ENIG gold, HASL silver).

Driven by the layer masks §5.4 generates, so soldermask correctly opens over
pads and silkscreen sits above it. Presets for the common looks, all parameters
exposed.

### 5.6 Parts library

Visual fidelity over footprint accuracy — modeled to look right at render
distance, not to match a datasheet's courtyard.

Seed set weighted to the headset case:

- **Ports:** USB-C receptacle (the one that matters), USB-A, micro-B
- **Controls:** tactile switches (several heights, since cap clearance is the
  whole point), slide switches, rotary encoder
- **Audio-adjacent:** driver solder pads, spring contacts, FPC/ZIF connectors,
  JST PH/SH
- **Generic fill:** SMD passives (0402/0603/0805), LEDs, QFN/QFP/SOIC ICs,
  crystals, inductors
- **Mechanical:** M1.6/M2 mounting holes, standoffs, castellated edges

Parts are collections with custom properties, indexed via the Asset Browser.
Adding a part = dropping in a folder, no code.

### 5.7 Fit checks — mechanical, not electrical

DRC is gone; what replaces it is "does this actually fit in the product":

- Part collides with an obstacle (the audio driver, shell ribs)
- Board or part protrudes through the cavity wall
- `TARGET`-anchored part has drifted from its target beyond tolerance —
  **catches the button-cap misalignment case directly**
- Component height exceeds available headroom at its location
- Mounting holes not reachable by their bosses

Reported as a GPU overlay plus a click-to-select list. These are the failures
that actually show up in a render.

### 5.8 Cables and wire runs

Curve-based: pick a connector or pad, pick a destination, get a wire with
realistic bevel, gauge, insulation material, and catenary sag. Bundles for
multi-conductor, plus flat ribbon and FPC flex strips — the latter being what
usually connects a board to a driver in a real headset.

Scoped modestly: the user's brief says "maybe some wire or something," so this
targets plausible rather than comprehensive.

### 5.9 Presentation

Small phase, disproportionate payoff for product visuals: cutaway (section
plane through the assembly), exploded view (parts offset along normals by a
single slider), and LOD control for background boards.

---

## 6. Phasing

**Phase 0 — Scaffold (small).** Manifest, registration, N-panel, unit setup, CI
running `pytest` on `core/`, headless smoke test.

**Phase 1 — Board geometry (medium).** Drawn outline, then context-derived
outline from cavity + obstacles. Thickness, cutouts, fillets, stable edge IDs.
*Usable: a board that fits the headset cavity and clears the driver.*

**Phase 2 — Parts + `TARGET` anchoring (large).** Data model, library, seed
parts, modal placer, external-object anchoring, outline-follows-target. *Usable:
switches under the button caps, USB at the shell opening — the actual brief.*

**Phase 3 — Visual detail (large).** Trace generator, pads, vias, silkscreen,
ground pour, passive scatter. Texture realizer first, geometry realizer after.
*Usable: it reads as a real board.*

**Phase 4 — Materials (medium).** Layered stack-up shader, soldermask and finish
presets.

**Phase 5 — Fit checks + cables + presentation (medium).**

**Phase 6 — Polish.** Docs, examples, LOD, possible extension-platform release.

**Phases 1–3 are the product.** A board that fits its cavity, parts that land
under their caps, and detail that reads as real — that's the whole brief. 4–6
are refinement.

Notable: the previous plan's Phase 5 (export) is deleted outright, and its
riskiest item (edge identity across outline edits) drops in severity, since
`TARGET` anchors bind to external objects that don't churn when the outline
changes. Edge identity still matters for `EDGE` parts, just less catastrophically.

---

## 7. Testing

- `core/` — pytest, no Blender. Polygon ops, anchor resolution, trace routing
  determinism (same seed → same layout).
- Operators — headless `blender --background --python` scripted scenarios.
- Visual — reference renders of a fixed scene, compared per release. Loose
  thresholds; this catches "the shader broke," not sub-pixel drift.
- CI — Blender 4.2 LTS and 5.2 LTS.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Traces look procedurally fake — the failure mode that undermines everything | Prototype the router in Phase 3 against reference photos early; tune on density and bundling, not on rule count |
| Context-derived outlines produce garbage on complex cavities | Always allow falling back to a drawn outline; treat derivation as a starting point that can be frozen and edited |
| `TARGET` bindings break when the headset model is restructured | Bind by object reference with a name fallback; report broken bindings loudly rather than silently reverting to last position |
| Library scope creep | Seed set is deliberately small and headset-weighted; user-authored parts are a first-class path |
| Texture resolution insufficient for close-ups | Geometry realizer shares the routing data — same source, different output |

---

## Sources

- [How to Create Extensions — Blender 5.2 LTS Manual](https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html)
- [Add-on Development Setup — Blender Developer Documentation](https://developer.blender.org/docs/handbook/extensions/addon_dev_setup/)
