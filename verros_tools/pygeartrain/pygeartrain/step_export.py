"""Write a SolidModel (see pygeartrain.solid_model) as STEP files with CadQuery.

CadQuery is an optional dependency: every public function raises a RuntimeError
with install instructions when it is missing.

Conventions (matching the solid_model schema):

* Each part is built centred at the origin with its mid plane at z = 0.
* ``half_twist_deg`` is the signed rotation of the +thickness/2 face relative to
  the mid plane, positive = counter-clockwise when viewed from +z.  CadQuery's
  twisted extrusion (``Solid.extrudeLinearWithRotation`` / ``twistExtrude``)
  uses the same sign: a corner at +11.3 deg on the bottom face ends up at +41.3
  deg on the top face for a +30 deg twist (measured, see test_step_export).
* An instance is placed by rotating the part about z by ``angle_deg``,
  translating by ``(x_mm, y_mm)`` and lifting it to ``layer * (thickness + gap)``.
"""
import math
import os
from typing import Dict, List, Optional, Sequence

try:
    import cadquery as cq
    from cadquery import Vector
except ImportError:  # pragma: no cover - exercised only without cadquery
    cq = None
    Vector = None

CURVE_MODES = ('spline', 'polyline')


def _require_cadquery():
    if cq is None:
        raise RuntimeError('STEP export needs CadQuery: pip install cadquery')


def _wire(points: Sequence[Sequence[float]], curves: str):
    """Closed wire through the loop points (last point != first)."""
    vecs = [Vector(float(x), float(y), 0.0) for x, y in points]
    if len(vecs) < 3:
        raise ValueError('A loop needs at least 3 points.')
    if curves == 'spline':
        edge = cq.Edge.makeSpline(vecs, periodic=True)
        return cq.Wire.assembleEdges([edge])
    if curves == 'polyline':
        return cq.Wire.makePolygon(vecs, close=True)
    raise ValueError(f"curves must be one of {CURVE_MODES}, got {curves!r}")


def _single_solid(shape):
    """Reduce a boolean result to its single Solid when possible."""
    try:
        solids = shape.Solids()
    except Exception:
        return shape
    if len(solids) == 1:
        return solids[0]
    return shape


def _face_solid(face: Dict, thickness_mm: float, gear_type: str, half_twist_deg: float, curves: str):
    outer = _wire(face['outer'], curves)
    holes = [_wire(h, curves) for h in face.get('holes', []) or []]
    half = thickness_mm / 2.0
    gear_type = gear_type.lower()
    if gear_type not in ('spur', 'helix', 'herringbone'):
        raise ValueError(f'Unknown gear_type {gear_type!r}')

    if gear_type == 'spur' or abs(half_twist_deg) < 1e-9:
        solid = cq.Solid.extrudeLinear(outer, holes, Vector(0, 0, thickness_mm))
        return solid.translate(Vector(0, 0, -half))

    if gear_type == 'helix':
        # twist by the full angle, then rotate back by half so the mid plane
        # carries the untwisted profile
        solid = cq.Solid.extrudeLinearWithRotation(
            outer, holes, Vector(0, 0, 0), Vector(0, 0, thickness_mm), 2.0 * half_twist_deg)
        solid = solid.rotate(Vector(0, 0, 0), Vector(0, 0, 1), -half_twist_deg)
        return solid.translate(Vector(0, 0, -half))

    # herringbone: upper half twisted by half_twist, lower half is its mirror image
    upper = cq.Solid.extrudeLinearWithRotation(
        outer, holes, Vector(0, 0, 0), Vector(0, 0, half), half_twist_deg)
    lower = upper.mirror('XY')
    return _single_solid(upper.fuse(lower, glue=True))


def solid_from_part(part: Dict, thickness_mm: float, gear_type: str, curves: str = 'spline'):
    """Build one part as a CadQuery Shape (Solid, or Compound when the faces do
    not merge into a single solid), centred at the origin with the mid plane at z = 0.

    ``curves='spline'`` interpolates a periodic B-spline through each loop,
    ``'polyline'`` connects the points with straight segments.
    """
    _require_cadquery()
    faces = part.get('faces') or []
    if not faces:
        raise ValueError(f"Part {part.get('name')!r} has no faces.")
    half_twist = float(part.get('half_twist_deg', 0.0) or 0.0)
    solids = [_face_solid(f, float(thickness_mm), gear_type, half_twist, curves) for f in faces]
    if len(solids) == 1:
        return solids[0]
    if part.get('fuse', False):
        return _single_solid(solids[0].fuse(*solids[1:]).clean())
    return cq.Compound.makeCompound(solids)


def place_instance(shape, instance: Dict, thickness_mm: float, layer_gap_mm: float):
    """Rotate/translate a part shape to where an instance sits in the assembly."""
    _require_cadquery()
    angle = float(instance.get('angle_deg', 0.0))
    z = float(instance.get('layer', 0)) * (float(thickness_mm) + float(layer_gap_mm))
    placed = shape.rotate(Vector(0, 0, 0), Vector(0, 0, 1), angle) if abs(angle) > 1e-12 else shape
    return placed.translate(Vector(float(instance.get('x_mm', 0.0)), float(instance.get('y_mm', 0.0)), z))


def build_solids(model: Dict, curves: str = 'spline') -> Dict[str, object]:
    """All parts of a SolidModel as {name: Shape}."""
    _require_cadquery()
    thickness = float(model['thickness_mm'])
    gear_type = model.get('gear_type', 'spur')
    return {p['name']: solid_from_part(p, thickness, gear_type, curves) for p in model['parts']}


def build_assembly(model: Dict, solids: Optional[Dict[str, object]] = None, curves: str = 'spline'):
    """All instances placed per the schema, as a single Compound."""
    _require_cadquery()
    if solids is None:
        solids = build_solids(model, curves)
    thickness = float(model['thickness_mm'])
    gap = float(model.get('layer_gap_mm', 0.0))
    placed = [place_instance(solids[inst['part']], inst, thickness, gap)
              for inst in model.get('instances', []) if inst['part'] in solids]
    if not placed:
        raise ValueError('Model has no instances to assemble.')
    return cq.Compound.makeCompound(placed)


def _safe_name(name: str) -> str:
    return ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in name) or 'part'


def write_step(model: Dict, out_dir: str, parts: bool = True, assembly: bool = True,
               curves: str = 'spline') -> List[str]:
    """Write ``<name>.step`` per part (centred at the origin) and ``assembly.step``
    with every instance placed.  Returns the written file paths."""
    _require_cadquery()
    if curves not in CURVE_MODES:
        raise ValueError(f"curves must be one of {CURVE_MODES}, got {curves!r}")
    os.makedirs(out_dir, exist_ok=True)
    solids = build_solids(model, curves)
    files = []
    if parts:
        for name, shape in solids.items():
            path = os.path.join(out_dir, _safe_name(name) + '.step')
            shape.exportStep(path)
            files.append(path)
    if assembly:
        compound = build_assembly(model, solids, curves)
        path = os.path.join(out_dir, 'assembly.step')
        compound.exportStep(path)
        files.append(path)
    return files
