# Blender PCB Generator

A Blender extension for designing PCBs shape-first: draw an arbitrary board
outline, populate it with connectors, ports, and controls that carry placement
*rules* rather than fixed coordinates, then export the outline and placement
downstream to an EDA tool.

The premise: Blender is good at shape and EDA tools are not. This inverts the
usual order — mechanical intent first, electrical layout second.

**Status:** planning. No code yet.

- [Build plan](docs/PLAN.md) — architecture, subsystems, phasing, risks
- [Open questions](docs/OPEN-QUESTIONS.md) — decisions needed before Phase 0

## Scope

Owns: board outline geometry, mechanical constraints, modular part placement,
clearance checks, enclosure interface geometry.

Does not own: traces, netlists, routing, copper, simulation, Gerbers.
