"""Describe a gear train as 3D solids, ready for a CAD kernel.

The description is plain data (JSON-able) so it can be consumed both by the
browser (OpenCascade.js / replicad) and by Python (CadQuery).  Geometry is in
millimetres, already scaled like cad_export.

Schema (SolidModel)
-------------------
{
  "units": "mm",
  "thickness_mm": 10.0,          # face width of every part
  "layer_gap_mm": 1.0,           # axial gap between stacked stages
  "gear_type": "spur" | "helix" | "herringbone",
  "scale_factor": 1.17,          # unscaled profile units -> mm
  "parts": [
    {
      "name": "ring_30",
      "internal": true,          # material lies OUTSIDE the profile loop (ring gear); faces already
                                 # include the outer circle with the tooth loop as a hole
      "fuse": false,             # true: faces are separate overlapping regions (no holes) to be
                                 # unioned into one solid, e.g. a carrier plate with its pins
      "outer_radius_mm": 38.5,   # only for internal parts: radius of the outer boundary circle
      "ref_radius_mm": 35.0,     # radius at which the helix angle is measured (max radius)
      "half_twist_deg": 4.3,     # signed rotation of the +thickness/2 face relative to the mid plane.
                                 # helix: the -thickness/2 face is rotated by -half_twist_deg
                                 # herringbone: the -thickness/2 face is rotated by +half_twist_deg
                                 # spur: 0
      "faces": [                 # one or more planar regions; each is extruded into a solid
        {"outer": [[x, y], ...], # closed loop, counter-clockwise, last point != first
         "holes": [[[x, y], ...], ...]}   # clockwise loops inside outer
      ]
    }
  ],
  "instances": [                 # where copies of the parts sit in the assembled mechanism
    {"part": "ring_30", "angle_deg": 0.0, "x_mm": 0.0, "y_mm": 0.0, "layer": 0}
  ]
}

An instance is placed by rotating the part about its own z axis by angle_deg,
translating by (x_mm, y_mm) and lifting it to z = layer * (thickness + gap),
with the part's mid plane at that z.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from pygeartrain import cad_export
from pygeartrain.core.geometry import flatten

INTERNAL_PREFIXES = ('ring_', 'stage1_ring_', 'stage2_ring_', 'outer_')
INTERNAL_MARGIN = 0.12  # outer boundary radius of a ring gear = max radius * (1 + margin)


def is_internal(name: str) -> bool:
    """Ring gears: material lies outside the tooth loop.  Pin rings are solid pins, not internal."""
    return name.startswith(INTERNAL_PREFIXES) and 'pins' not in name


def is_fused(name: str) -> bool:
    """Parts drawn as several overlapping/touching loops that form one solid
    (e.g. an output carrier plate with its pins, or the eccentric cam with its shaft)."""
    return 'pins' in name or name == 'eccentric'


def drop_contained_loops(loops: Sequence[np.ndarray]) -> List[np.ndarray]:
    """For a union of regions, a loop lying inside another loop adds nothing to a
    2D extrusion (and tangent inner circles make invalid solids): drop them."""
    from shapely.geometry import Polygon

    polys = [Polygon(l) for l in loops]
    keep = []
    for i, pi in enumerate(polys):
        # tolerance covers the chord error of the polygonised circles (tangent inner circles)
        inside = any(j != i and abs(pj.area) > abs(pi.area)
                     and pj.buffer(2e-3 * math.sqrt(abs(pj.area) / math.pi)).contains(pi)
                     for j, pj in enumerate(polys))
        if not inside:
            keep.append(loops[i])
    return keep


@dataclass
class SolidSettings:
    target_diameter_mm: float = 70.0
    thickness_mm: float = 10.0
    helix_angle_deg: float = 0.0
    gear_type: str = 'spur'
    layer_gap_mm: float = 1.0
    ring_margin: float = INTERNAL_MARGIN


# ---------------------------------------------------------------------------
# loops
# ---------------------------------------------------------------------------

def profile_loops(profile) -> List[np.ndarray]:
    """Split a Profile (possibly a concatenation of several closed curves) into
    its closed vertex loops, each an (n, 2) array without repeated end point."""
    edges = np.asarray(profile.topology.elements[1])
    verts = np.asarray(profile.vertices, dtype=float)
    if len(edges) == 0:
        return []
    nxt = {}
    for a, b in edges:
        nxt[int(a)] = int(b)
    seen = set()
    loops = []
    for start in nxt:
        if start in seen:
            continue
        loop = []
        v = start
        while v not in seen and v in nxt:
            seen.add(v)
            loop.append(v)
            v = nxt[v]
        if len(loop) >= 3:
            pts = verts[loop]
            # drop consecutive duplicates (and a duplicated closing point)
            keep = np.ones(len(pts), bool)
            keep[1:] = np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-12
            pts = pts[keep]
            if len(pts) >= 3 and np.linalg.norm(pts[0] - pts[-1]) <= 1e-12:
                pts = pts[:-1]
            if len(pts) >= 3:
                loops.append(pts)
    return loops


def signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _orient(pts: np.ndarray, ccw: bool) -> np.ndarray:
    return pts if (signed_area(pts) > 0) == ccw else pts[::-1].copy()


def nest_loops(loops: Sequence[np.ndarray]) -> List[Dict]:
    """Group loops into faces: {'outer': loop, 'holes': [loops]}.

    A loop at even containment depth is an outer boundary; a loop at odd depth is
    a hole of its immediate parent.  (Islands inside holes become new faces.)
    """
    from shapely.geometry import Polygon

    polys = [Polygon(l) for l in loops]
    order = sorted(range(len(loops)), key=lambda i: -abs(polys[i].area))
    parent: Dict[int, Optional[int]] = {}
    for i in order:
        rep = polys[i].representative_point()
        best = None
        for j in order:
            if j == i or abs(polys[j].area) <= abs(polys[i].area):
                continue
            if polys[j].contains(rep):
                if best is None or abs(polys[j].area) < abs(polys[best].area):
                    best = j
        parent[i] = best

    def depth(i):
        d = 0
        while parent[i] is not None:
            i = parent[i]
            d += 1
        return d

    faces = {}
    for i in order:
        if depth(i) % 2 == 0:
            faces[i] = {'outer': _orient(loops[i], ccw=True), 'holes': []}
    for i in order:
        if depth(i) % 2 == 1:
            faces[parent[i]]['holes'].append(_orient(loops[i], ccw=False))
    return [faces[i] for i in order if i in faces]


# ---------------------------------------------------------------------------
# rigid placement recovery (Kabsch in 2D)
# ---------------------------------------------------------------------------

def rigid_fit(src: np.ndarray, dst: np.ndarray) -> Tuple[float, np.ndarray, float]:
    """Find angle, t such that dst ~ R(angle) src + t.  Returns (angle, t, rms residual)."""
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    H = (src - cs).T @ (dst - cd)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, d]) @ U.T
    t = cd - R @ cs
    res = dst - (src @ R.T + t)
    angle = math.atan2(R[1, 0], R[0, 0])
    return angle, t, float(np.sqrt((res ** 2).mean()))


# ---------------------------------------------------------------------------
# per gear type: which arranged profiles belong to which layer
# ---------------------------------------------------------------------------

def _layers(spec_name_or_gear, gear) -> List[Tuple[object, int]]:
    """List of (arranged profile, layer index) at phase 0."""
    arranged = gear.arrange(0)
    cls = type(gear).__name__
    if cls in ('CompoundPlanetaryGeometry', 'CompoundCycloidGeometry'):
        p1, p2 = arranged
        return [(p, 0) for p in flatten(p1)] + [(p, 1) for p in flatten(p2)]
    if cls == 'NabtescoGeometry':
        C, P = arranged
        return [(p, 0) for p in flatten(C)] + [(p, 1) for p in flatten(P)]
    return [(p, 0) for p in flatten(arranged)]


def compute_placements(gear, item_vertices: Dict[str, np.ndarray]) -> List[Dict]:
    """Match every arranged profile (phase 0) to an export item and recover its rigid
    placement.  Returns dicts {part, angle_deg, t (unscaled 2-vector), layer}."""
    placements = []
    used: Dict[str, int] = {name: 0 for name in item_vertices}
    for profile, layer in _layers(None, gear):
        w = np.asarray(profile.vertices, float)
        if len(w) < 3:
            continue
        matches = []
        for name, v in item_vertices.items():
            if len(v) != len(w):
                continue
            angle, t, rms = rigid_fit(v, w)
            if rms < 1e-7 * max(1.0, float(np.abs(v).max())):
                matches.append((name, angle, t))
        if not matches:
            continue
        # geometrically identical parts (e.g. a sun and a planet with equal teeth and module)
        # are disambiguated by stage name, then by spreading instances over the candidates
        staged = [m for m in matches if m[0].startswith(f'stage{layer + 1}_')]
        if staged:
            matches = staged
        name, angle, t = min(matches, key=lambda m: used[m[0]])
        used[name] += 1
        placements.append({'part': name, 'angle_deg': math.degrees(angle), 't': t, 'layer': layer})
    return placements


def dimensions(gear, export_spec: Dict, target_diameter_mm: float) -> Dict:
    """Key dimensions in mm at the given export scale, without building any solids.

    rows: one per part with outer_diameter_mm, count (instances in the assembly),
          center_offset_mm (distance of the part axis from the main axis) and, when
          several copies share that offset, center_circle_diameter_mm.
    headline: the single most useful number, e.g. the planet-centre circle of a
          planetary ('Planet centre circle') or the eccentricity of a cycloid disc.
    """
    items = [it for it in export_spec['items'] if it.vertices is not None and len(it.vertices) >= 3]
    if not items:
        return {'scale_factor': 1.0, 'rows': [], 'headline': None}
    ref_name = export_spec.get('reference')
    ref_item = next((it for it in items if it.name == ref_name), None) or         max(items, key=lambda it: np.max(np.linalg.norm(it.vertices, axis=1)))
    scale = cad_export.compute_scale_factor(ref_item.vertices, target_diameter_mm)
    item_vertices = {it.name: np.asarray(it.vertices, float) for it in items}
    placements = compute_placements(gear, item_vertices)

    rows = []
    for it in items:
        mine = [pl for pl in placements if pl['part'] == it.name]
        offsets = [float(np.hypot(*pl['t'])) * scale for pl in mine]
        offset = max(offsets) if offsets else 0.0
        row = {
            'part': it.name,
            'outer_diameter_mm': float(2 * np.max(np.linalg.norm(it.vertices, axis=1)) * scale),
            'count': len(mine),
            'center_offset_mm': offset,
            'center_circle_diameter_mm': 2 * offset if offset > 1e-9 else 0.0,
            'layer': mine[0]['layer'] if mine else 0,
        }
        rows.append(row)

    headline = None
    multi = [r for r in rows if r['count'] >= 2 and r['center_offset_mm'] > 1e-9]
    single = [r for r in rows if r['count'] == 1 and r['center_offset_mm'] > 1e-9]
    if multi:
        r = multi[0]
        kind = 'Planet' if 'planet' in r['part'] else ('Wobbler' if 'wobbler' in r['part'] else 'Part')
        headline = {'label': f"{kind} centre circle diameter", 'value_mm': r['center_circle_diameter_mm'],
                    'detail': f"{r['count']} x {r['part']}, each {r['center_offset_mm']:.3f} mm from the axis"}
    elif len(single) >= 2:
        # two offset parts (e.g. a gear pair): the centre distance between them
        a, b = single[0], single[1]
        ta = next(pl['t'] for pl in placements if pl['part'] == a['part'])
        tb = next(pl['t'] for pl in placements if pl['part'] == b['part'])
        headline = {'label': f"Centre distance {a['part']} to {b['part']}", 'value_mm': float(np.hypot(*(ta - tb))) * scale,
                    'detail': 'distance between the two part axes'}
    elif single:
        r = single[0]
        headline = {'label': f"{r['part']} centre offset (eccentricity)", 'value_mm': r['center_offset_mm'],
                    'detail': 'distance between the part axis and the main axis'}
    return {'scale_factor': float(scale), 'reference': ref_item.name,
            'target_diameter_mm': float(target_diameter_mm), 'rows': rows, 'headline': headline}


def build_solid_model(gear, export_spec: Dict, settings: SolidSettings) -> Dict:
    """export_spec is the dict returned by a cad_export.*_items adapter."""
    items: List[cad_export.ExportItem] = [it for it in export_spec['items']
                                          if it.vertices is not None and len(it.vertices) >= 3]
    if not items:
        raise ValueError('Nothing to export: no profiles.')
    ref_name = export_spec.get('reference')
    ref_item = next((it for it in items if it.name == ref_name), None) or \
        max(items, key=lambda it: np.max(np.linalg.norm(it.vertices, axis=1)))
    scale = cad_export.compute_scale_factor(ref_item.vertices, settings.target_diameter_mm)

    gear_type = settings.gear_type.lower()
    tan_helix = 0.0 if gear_type == 'spur' else math.tan(math.radians(settings.helix_angle_deg))
    half = settings.thickness_mm / 2.0

    # the base profiles as given by generate_profiles, needed to recover placements
    base_profiles = [p for p in flatten(gear.generate_profiles) if hasattr(p, 'vertices')]

    parts = []
    item_vertices = {}
    for it in items:
        # find the Profile object this item was made from, to split it into loops
        profile = next((p for p in base_profiles if len(p.vertices) == len(it.vertices)
                        and np.allclose(p.vertices, it.vertices)), None)
        if profile is None:
            loops = [np.asarray(it.vertices, float)]
        else:
            loops = profile_loops(profile)
        loops = [l * scale for l in loops]
        if not loops:
            continue
        ref_radius = float(max(np.max(np.linalg.norm(l, axis=1)) for l in loops))
        internal = is_internal(it.name)
        fused = is_fused(it.name)
        if fused:
            faces = [{'outer': _orient(l, ccw=True), 'holes': []} for l in drop_contained_loops(loops)]
        else:
            faces = nest_loops(loops)
        if internal:
            outer_r = ref_radius * (1 + settings.ring_margin)
            n = 360
            a = np.linspace(0, 2 * np.pi, n, endpoint=False)
            circle = np.column_stack([outer_r * np.cos(a), outer_r * np.sin(a)])
            # the tooth loop(s) become holes of a disc
            holes = [_orient(f['outer'], ccw=False) for f in faces]
            faces = [{'outer': circle, 'holes': holes}]
        half_twist = 0.0 if gear_type == 'spur' or it.hand == 0 or ref_radius < 1e-9 \
            else half * tan_helix * it.hand / ref_radius
        parts.append({
            'name': it.name,
            'internal': bool(internal),
            'fuse': bool(fused),
            'outer_radius_mm': float(ref_radius * (1 + settings.ring_margin)) if internal else None,
            'ref_radius_mm': ref_radius,
            'half_twist_deg': math.degrees(half_twist),
            'faces': [{'outer': f['outer'].round(8).tolist(), 'holes': [h.round(8).tolist() for h in f['holes']]}
                      for f in faces],
        })
        item_vertices[it.name] = np.asarray(it.vertices, float)

    instances = [{'part': pl['part'], 'angle_deg': pl['angle_deg'],
                  'x_mm': float(pl['t'][0] * scale), 'y_mm': float(pl['t'][1] * scale), 'layer': pl['layer']}
                 for pl in compute_placements(gear, item_vertices)]

    return {
        'units': 'mm',
        'thickness_mm': float(settings.thickness_mm),
        'layer_gap_mm': float(settings.layer_gap_mm),
        'gear_type': gear_type,
        'scale_factor': float(scale),
        'parts': parts,
        'instances': instances,
    }
