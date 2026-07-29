# Blender PCB Generator

Give it a shape. Get back a believable PCB.

A Blender extension that turns a shape you've already decided on into something
that reads as a real circuit board in a render — materials, procedural traces
and silkscreen, scattered small components, and precise control over the parts
whose position actually matters.

Library PCB models never fit: wrong outline, wrong size, connectors in the wrong
place. This generates one shaped to what you're building.

**Status:** planning. No code yet.

- [Build plan](docs/PLAN.md) — architecture, subsystems, phasing, risks
- [Open questions](docs/OPEN-QUESTIONS.md) — decisions needed before Phase 0

## The loop

```
shape  →  board  →  materials  →  procedural detail  →  scattered parts  →  placed parts
                                  traces, silkscreen,    random fill        ports, buttons —
                                  pads, vias                                exact control
```

Shape comes in three ways: draw a curve, extract a boundary from existing
geometry, or point at an object that already is the board.

## Scope

**Owns:** board geometry from a given shape, board materials, procedural detail
generation, component scatter, precise component placement, point-to-point wires.

**Does not own:** fabrication output, electrical correctness, netlists, real
routing, EDA interop — or deciding what shape your board should be. You bring
the shape.
