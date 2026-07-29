"""Unit handling.

The addon works in millimetres throughout. Blender scenes are configured with
``unit_settings.scale_length = 0.001`` so that one Blender unit displays as one
millimetre, which means core geometry can use raw millimetre floats with no
conversion at all. These helpers exist for the few places that still need to
reason about pitch and rounding.
"""

from __future__ import annotations

# Common component pitches, millimetres.
PITCH_254 = 2.54
PITCH_200 = 2.00
PITCH_127 = 1.27
PITCH_100 = 1.00
PITCH_050 = 0.50

STANDARD_PITCHES = (PITCH_254, PITCH_200, PITCH_127, PITCH_100, PITCH_050)

# Board thicknesses that actually get manufactured, millimetres.
STANDARD_THICKNESSES = (0.6, 0.8, 1.0, 1.2, 1.6, 2.0)

DEFAULT_THICKNESS = 1.6

# Blender's scale_length for a millimetre-native scene.
SCENE_SCALE_LENGTH = 0.001


def snap(value: float, pitch: float, origin: float = 0.0) -> float:
    """Round ``value`` to the nearest multiple of ``pitch`` offset by ``origin``."""
    if pitch <= 0.0:
        return value
    return origin + round((value - origin) / pitch) * pitch


def snap_point(point, pitch: float, origin=(0.0, 0.0)):
    """Snap a 2D point to a pitch grid."""
    return (snap(point[0], pitch, origin[0]), snap(point[1], pitch, origin[1]))


def snap_angle(radians: float, step_degrees: float = 90.0) -> float:
    """Round an angle to the nearest ``step_degrees`` increment."""
    import math

    if step_degrees <= 0.0:
        return radians
    step = math.radians(step_degrees)
    return round(radians / step) * step


def nearest_standard_thickness(value: float) -> float:
    """Closest manufacturable board thickness to ``value``."""
    return min(STANDARD_THICKNESSES, key=lambda t: abs(t - value))
