# Open Questions

Decisions that change the shape of the build. Roughly in order of how much they
cost to change later.

---

### Q1 — What is the output actually for? *(blocks Phase 5, shapes everything)*

- **(a) Mechanical/ID tool** — outline + connector placement is the source of
  truth, exported to KiCad for the electrical work. Export fidelity matters
  enormously; render quality is secondary.
- **(b) Visualization** — mockups, product renders, enclosure fitting. Nothing
  goes to fab. Materials and part realism matter; DXF/KiCad export barely does.
- **(c) Both**, with (a) leading.

The plan currently assumes **(a)**. If it's really (b), Phase 5 shrinks to
almost nothing and Phase 2's library needs far more visual fidelity instead.

---

### Q2 — How do you want to draw the shape?

- Bézier/poly **curve** object (most precise, dimension-driven)
- **Grease Pencil** sketch, auto-fitted (fastest, loosest)
- **Mesh** face boundary (familiar to box-modelers)
- Parametric **primitives** + boolean ops (rounded rect, D-shape, etc.)

Plan supports curve first, others after. Worth knowing which one you'd actually
reach for, since that one should be excellent rather than merely present.

---

### Q3 — Which parts matter to you first?

The seed library list in PLAN.md §5.3 is a guess. If there's a specific project
driving this — a particular connector set, a form factor, a board family — that
list should be replaced with the real one. Building 40 parts nobody needs while
missing the one you do is the easiest way to waste Phase 2.

---

### Q4 — Bundle `shapely`, or stay dependency-free?

Bundling gives correct polygon offsetting for free but adds a wheel (~5 MB) and
an install-failure surface. Dependency-free means hand-rolling offsetting, which
is a real source of subtle bugs on concave shapes.

Plan recommends **bundling**. Cheap to reverse early, expensive later.

---

### Q5 — How much does KiCad specifically matter?

Writing `.kicad_pcb` directly is meaningfully more work than DXF + a placement
CSV, and it's version-sensitive. If KiCad is *the* downstream tool, it's worth
it. If the workflow is "import outline as Edge.Cuts and place by hand anyway,"
DXF + CSV is plenty and Phase 5 gets much shorter.

Also: does anything need to come *back* from KiCad (footprint positions changed
during layout)? Round-trip is a much bigger commitment than one-way export and
isn't in the current plan.

---

### Q6 — Enclosure cutout generation: core feature or nice-to-have?

Generating boolean solids from connector openings so you can subtract them from
a case is, I think, the feature that makes this worth doing in Blender
specifically rather than in an EDA tool. It's listed as Phase 5 P1.

If you agree it's central, it should move earlier — arguably right after
Phase 2 — because it changes what metadata every library part needs to carry,
and retrofitting metadata across a built library is annoying.

---

### Q7 — Blender version floor

Plan targets 4.2 LTS minimum. Dropping to 5.x-only removes a compatibility
burden and unlocks newer APIs. Supporting anything below 4.2 means maintaining
the legacy `bl_info` addon path alongside the extension manifest — not
recommended.

---

### Q8 — Who else uses this?

Just you, or is this meant to be published to the Blender extensions platform?
Publishing implies docs, versioned releases, a support surface, and stricter
dependency hygiene. It's a real cost and worth deciding before Phase 0 rather
than after.
