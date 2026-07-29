# Open Questions

Revised after the scope pivot to visualization-only. The previous round's
questions about fab output, KiCad, and export fidelity are resolved and removed.

---

## Answered

- **Purpose** — visualization first, foremost, possibly solely. No fabrication
  output. Removes an entire phase from the plan.
- **Dependencies** — `shapely` no longer justified once exactness stops
  mattering. Zero dependencies.
- **Driving case** — headset internal PCB: next to an audio driver, USB port,
  tact switches under externally-modeled plastic caps, possibly a wire run.

---

## Blocking

### Q1 — How does the addon see the headset model?

The whole context-derived-outline and `TARGET`-anchor design assumes the
surrounding geometry is *in the scene* and readable.

- Is the headset already modeled in Blender, or coming from CAD (STEP/OBJ/FBX)?
- Is the audio driver a real solid, or a placeholder?
- For button caps: is there anything I can bind to — an empty, a named object,
  a marked face — or would you be placing switches by eye against a visual
  reference?

If the caps are bindable objects, Phase 2 delivers the brief almost by itself.
If they're not, the first useful step is a small workflow for *marking* target
points on existing geometry, which is a different (smaller) piece of work.

### Q2 — How close does the camera get?

This sets the fidelity budget for everything in Phase 3.

- **Background/context** — visible through a cutaway or in an exploded diagram,
  never hero. Texture traces are plenty; no geometry realizer needed.
- **Mid** — clearly readable, fills part of frame. Texture traces at high
  resolution, real geometry for parts.
- **Hero close-up** — individual components legible. Needs the geometry
  realizer, proper pad finishes, and much more part detail.

I've planned for mid with a path to hero. If it's genuinely background-only,
Phase 3 shrinks by half.

### Q3 — Single- or double-sided?

Double-sided doubles part placement UI, adds bottom-side trace generation, and
matters a lot for a thin headset cavity where the board is sandwiched. Real
headset boards are usually populated both sides — but if the reverse is never
visible in your shots, it's free to skip.

---

## Non-blocking

### Q4 — Rigid board, or flex/rigid-flex?

Headsets very often use a flex ribbon from the main board to the driver, and
your "maybe some wire or something" might really be a flex tail. Flex means the
outline extrudes along a *curved* path rather than a flat plane — a meaningful
addition to Phase 1, but self-contained and deferrable.

Cheap version: a rigid board plus a separate FPC strip part (already in the
Phase 5 cable work). Expensive version: true rigid-flex with bend regions.

### Q5 — One board, or a family?

Building this for a single headset PCB versus building a tool you'll reuse
across products changes how much goes into the library and preset system. If
it's one board, some of Phase 6 is pointless and Phase 2's library shrinks to
just the parts you need.

### Q6 — Soldermask color and finish?

Trivially changeable later, but if the headset has an established look —
matte black board with ENIG gold, say — I'd rather build the material presets
around it than around generic green.

### Q7 — Does the board need mounting features?

Screw bosses, snap tabs, castellations, alignment notches. Visible in a cutaway,
invisible otherwise. Affects the seed library and the fit checks, not the
architecture.
