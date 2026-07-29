"""Operators. Kept thin — they validate, delegate to :mod:`..build`, and report."""

import bpy

from . import board_ops, component_ops, detail_ops, wire_ops

_MODULES = (board_ops, detail_ops, component_ops, wire_ops)


def register() -> None:
    for module in _MODULES:
        for cls in module.CLASSES:
            bpy.utils.register_class(cls)


def unregister() -> None:
    for module in reversed(_MODULES):
        for cls in reversed(module.CLASSES):
            bpy.utils.unregister_class(cls)
