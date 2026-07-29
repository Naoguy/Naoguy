"""Pure-Python core for the PCB generator.

Nothing in this package may import ``bpy``. Geometry, routing, scatter and
anchor resolution are plain Python over plain data so they can be unit tested
without launching Blender.
"""
