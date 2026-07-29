"""PCB Generator — turn any shape into a believable PCB for renders.

Registration only. All behaviour lives in the subpackages:

``core``
    Pure Python. Geometry, routing, scatter and anchor resolution, with no
    ``bpy`` import anywhere, so it is unit testable without Blender.
``build``
    Turns core's plain data into meshes and objects.
``ops`` / ``ui``
    Thin operator and panel layers.
"""

from . import data, ops, parts, shading, ui
from .data import properties


def register() -> None:
    properties.register()
    ops.register()
    ui.register()


def unregister() -> None:
    ui.unregister()
    ops.unregister()
    properties.unregister()
