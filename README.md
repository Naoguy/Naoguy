# Blender PCB Generator

A Blender extension for generating **believable PCBs for renders** — boards
shaped to fit a specific product cavity, populated with parts that land exactly
where the surrounding hardware needs them.

Library PCB models never fit: wrong outline, wrong size, connectors in the wrong
place, no relationship to the product they're supposed to live inside. This
generates one that belongs.

Driving case: a PCB inside a headset, next to an audio driver, with a USB port
at a shell opening and tactile switches sitting under externally-modeled plastic
button caps.

**Status:** planning. No code yet.

- [Build plan](docs/PLAN.md) — architecture, subsystems, phasing, risks
- [Open questions](docs/OPEN-QUESTIONS.md) — decisions needed before Phase 0

## Scope

**Owns:** board outline (drawn, or derived from a cavity and its obstacles),
part placement anchored to external scene geometry, procedural visual detail
(traces, silkscreen, pads, vias), board materials, mechanical fit checks,
cables.

**Does not own:** fabrication output of any kind, electrical correctness,
netlists, real routing, EDA interoperability — or the enclosure itself, which is
read as context rather than generated.
