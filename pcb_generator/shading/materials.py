"""Board and component materials.

A PCB's look comes from a layer stack, not a single surface: bare FR4 substrate,
etched copper, soldermask over the copper, silkscreen over the mask, and an
exposed metal finish wherever the mask opens for a pad. Modelling that as
separate materials on separate geometry — rather than one green shader — is what
makes traces read as slightly raised under the mask and pads read as bare metal.

Materials are created once and reused by name, so regenerating a board does not
leak duplicate datablocks.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

import bpy

RGBA = Tuple[float, float, float, float]

# Material names. Reused across regenerations.
MAT_SOLDERMASK = "PCB Soldermask"
MAT_SUBSTRATE = "PCB Substrate"
MAT_TRACE = "PCB Trace"
MAT_PAD = "PCB Pad"
MAT_SILKSCREEN = "PCB Silkscreen"
MAT_POUR = "PCB Pour"

MAT_PART_BODY = "PCB Part Body"
MAT_PART_METAL = "PCB Part Metal"
MAT_PART_PLASTIC = "PCB Part Plastic"
MAT_PART_LED = "PCB Part LED"
MAT_PART_CERAMIC = "PCB Part Ceramic"

MAT_WIRE = "PCB Wire"

# Part meshes all carry the same four slots so one assignment path serves
# every builder.
SLOT_BODY = 0
SLOT_METAL = 1
SLOT_PLASTIC = 2
SLOT_LED = 3
SLOT_CERAMIC = 4

PART_SLOTS = (
    MAT_PART_BODY,
    MAT_PART_METAL,
    MAT_PART_PLASTIC,
    MAT_PART_LED,
    MAT_PART_CERAMIC,
)


SOLDERMASK_COLORS: Dict[str, RGBA] = {
    "GREEN": (0.011, 0.135, 0.045, 1.0),
    "BLACK": (0.010, 0.011, 0.012, 1.0),
    "MATTE_BLACK": (0.006, 0.007, 0.008, 1.0),
    "BLUE": (0.010, 0.048, 0.180, 1.0),
    "RED": (0.220, 0.014, 0.012, 1.0),
    "WHITE": (0.720, 0.720, 0.700, 1.0),
    "PURPLE": (0.075, 0.014, 0.135, 1.0),
    "YELLOW": (0.420, 0.290, 0.020, 1.0),
}

FINISH_COLORS: Dict[str, RGBA] = {
    # ENIG is nickel under a very thin gold flash, so it reads warm but not
    # brassy. HASL is tinned copper — cooler and less reflective.
    "ENIG": (0.780, 0.560, 0.190, 1.0),
    "HASL": (0.640, 0.650, 0.660, 1.0),
    "OSP": (0.640, 0.380, 0.220, 1.0),
}

SUBSTRATE_COLOR: RGBA = (0.290, 0.250, 0.130, 1.0)
SILKSCREEN_COLOR: RGBA = (0.700, 0.700, 0.675, 1.0)


def _principled(
    name: str,
    base_color: RGBA,
    roughness: float = 0.5,
    metallic: float = 0.0,
    replace: bool = False,
    specular: Optional[float] = None,
    coat: float = 0.0,
) -> bpy.types.Material:
    """Fetch or build a Principled BSDF material."""
    material = bpy.data.materials.get(name)
    if material is not None and not replace:
        return material

    if material is None:
        material = bpy.data.materials.new(name)

    material.use_nodes = True
    tree = material.node_tree
    bsdf = tree.nodes.get("Principled BSDF")
    if bsdf is None:
        for node in tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                bsdf = node
                break
    if bsdf is None:
        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = tree.nodes.get("Material Output") or tree.nodes.new(
            "ShaderNodeOutputMaterial"
        )
        tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    _set(bsdf, "Base Color", base_color)
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Metallic", metallic)
    if specular is not None:
        # Renamed across Blender versions; try both.
        if not _set(bsdf, "Specular IOR Level", specular):
            _set(bsdf, "Specular", specular)
    if coat:
        if not _set(bsdf, "Coat Weight", coat):
            _set(bsdf, "Clearcoat", coat)

    # Viewport colour, so the board reads correctly in solid shading too.
    material.diffuse_color = base_color
    material.roughness = roughness
    material.metallic = metallic

    return material


def _set(node: bpy.types.Node, input_name: str, value) -> bool:
    socket = node.inputs.get(input_name)
    if socket is None:
        return False
    socket.default_value = value
    return True


def build_board_materials(
    mask_color: str = "GREEN",
    finish: str = "ENIG",
    gloss: float = 0.35,
    replace: bool = True,
) -> Dict[str, bpy.types.Material]:
    """Create the board stack-up materials for the given preset."""
    mask_rgba = SOLDERMASK_COLORS.get(mask_color, SOLDERMASK_COLORS["GREEN"])
    finish_rgba = FINISH_COLORS.get(finish, FINISH_COLORS["ENIG"])

    # Gloss 0 is a fully matte mask, 1 a wet-looking one.
    mask_roughness = 0.62 - 0.42 * max(0.0, min(1.0, gloss))

    materials = {
        MAT_SOLDERMASK: _principled(
            MAT_SOLDERMASK, mask_rgba, roughness=mask_roughness,
            coat=0.25 * gloss, replace=replace,
        ),
        # Board edges are routed FR4 — no mask, no gloss, visibly fibrous.
        MAT_SUBSTRATE: _principled(
            MAT_SUBSTRATE, SUBSTRATE_COLOR, roughness=0.85, replace=replace,
        ),
        # Copper under mask. The mask is opaque — a trace shows because it
        # lifts the mask a few microns, not because it shows through. Over
        # copper the mask film is thinner and smoother, so a trace reads
        # slightly darker and noticeably glossier than the mask beside it.
        # Making it lighter instead is what turns traces into stuck-on tape.
        MAT_TRACE: _principled(
            MAT_TRACE,
            tuple(c * 0.78 for c in mask_rgba[:3]) + (1.0,),
            roughness=max(0.08, mask_roughness - 0.16),
            coat=0.35 * gloss,
            replace=replace,
        ),
        MAT_PAD: _principled(
            MAT_PAD, finish_rgba, roughness=0.28, metallic=1.0, replace=replace,
        ),
        # Screen-printed epoxy ink: flat, chalky, never bright white.
        MAT_SILKSCREEN: _principled(
            MAT_SILKSCREEN, SILKSCREEN_COLOR, roughness=0.86, replace=replace,
        ),
        # The pour sits under mask like any other copper but reads flatter,
        # since it is a large unbroken area rather than a thin run.
        MAT_POUR: _principled(
            MAT_POUR,
            tuple(c * 0.92 for c in mask_rgba[:3]) + (1.0,),
            roughness=max(0.1, mask_roughness - 0.05),
            replace=replace,
        ),
    }
    return materials


def build_part_materials(replace: bool = False) -> Dict[str, bpy.types.Material]:
    """Materials shared by every generated component."""
    return {
        MAT_PART_BODY: _principled(
            MAT_PART_BODY, (0.020, 0.020, 0.022, 1.0), roughness=0.45, replace=replace
        ),
        # Reflowed solder and plated legs — never a mirror.
        MAT_PART_METAL: _principled(
            MAT_PART_METAL,
            (0.620, 0.625, 0.640, 1.0),
            roughness=0.34,
            metallic=1.0,
            replace=replace,
        ),
        MAT_PART_PLASTIC: _principled(
            MAT_PART_PLASTIC, (0.600, 0.595, 0.575, 1.0), roughness=0.60, replace=replace
        ),
        MAT_PART_LED: _principled(
            MAT_PART_LED, (0.900, 0.880, 0.840, 1.0), roughness=0.25, replace=replace
        ),
        # MLCC ceramic — the tan bodies that break up a board of black parts.
        MAT_PART_CERAMIC: _principled(
            MAT_PART_CERAMIC, (0.430, 0.330, 0.235, 1.0), roughness=0.62,
            replace=replace,
        ),
    }


def build_wire_material(
    color: RGBA = (0.020, 0.020, 0.022, 1.0), replace: bool = True
) -> bpy.types.Material:
    return _principled(MAT_WIRE, color, roughness=0.42, replace=replace)


def assign(obj: bpy.types.Object, names: Iterable[str]) -> None:
    """Replace an object's material slots with the named materials, in order."""
    data = obj.data
    if data is None:
        return

    data.materials.clear()
    for name in names:
        data.materials.append(bpy.data.materials.get(name))
