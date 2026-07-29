"""User interface."""

import bpy

from . import panels


def register() -> None:
    for cls in panels.CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(panels.CLASSES):
        bpy.utils.unregister_class(cls)
