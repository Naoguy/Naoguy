# Open Questions

Trimmed after the scope simplification. Questions about fab output, KiCad,
cavity fitting, and flex PCBs are resolved or dropped.

---

## Settled

- **Purpose** — visualization only. No fabrication output, no electrical
  correctness.
- **Shape** — you bring it. Three input paths (draw / extract from object /
  point at an existing object). No cavity analysis, no obstacle avoidance, no
  outline-follows-component.
- **Wires** — thin point-to-point runs, e.g. driver to a spot on the board. Not
  flex tails, not rigid-flex.
- **Dependencies** — zero.

---

## Blocking

### Q1 — How close does the camera get?

Sets the fidelity budget for Phase 3, the largest phase.

- **Background** — visible in a cutaway or exploded view, never hero. Texture
  traces at modest resolution; no geometry realizer. Phase 3 shrinks by roughly
  half.
- **Mid** — clearly readable, fills part of frame. High-res texture traces, real
  geometry for parts. **Planned for this.**
- **Hero** — individual components legible. Needs the geometry realizer, proper
  pad finishes, far more part detail.

### Q2 — Single- or double-sided?

Double-sided means bottom-side scatter, bottom-side routing, and a side toggle
throughout the placement UI. Real headset boards populate both — but if the
reverse never faces camera, it's free to skip and easy to add later.

---

## Non-blocking

### Q3 — What do wires attach to?

Endpoints need to be *something*. Cleanest is: click a point on the board
surface and a point on the target object, and the addon drops empties that the
wire binds to — so moving the driver drags its wires along. Alternative is
snapping to actual pads or vertices, which is more precise but more fragile when
geometry changes.

Defaulting to the empties approach unless you'd rather it snap to real geometry.

### Q4 — For path (c), does the object keep its own UVs?

If you point at an existing mesh and it already has a UV layout you care about,
detail generation can either respect it or generate its own. Respecting it is
better if the mesh came from CAD with deliberate UVs; regenerating is better if
it's an untextured block. Easy to support both — just want to know which is the
default.

### Q5 — Soldermask colour and finish?

Trivially changeable, but if the board has an established look — matte black
with ENIG gold, say — I'd rather build the presets around it than around
generic green.

### Q6 — One board, or reusable across products?

Affects how much goes into the library and preset system. If this is for a
single headset PCB, the seed library shrinks to just the parts you need and some
of Phase 6 is pointless.

### Q7 — Does the board need mounting features?

Screw holes, standoffs, castellations, alignment notches. Visible in a cutaway,
invisible otherwise. Affects the seed library, not the architecture.
