"""Export gear profiles as XYZ point curves for CAD import (e.g. SolidWorks
'Curve Through XYZ Points').

This generalizes the logic of generate_planetary_cad.py so it can be reused by
the GUI and by scripts for any gear train type.  Each profile is exported as
three closed curves: at Z=0 and at Z=+/- thickness/2.  For helical and
herringbone gears the +/- curves are rigidly rotated by the twist angle that a
helix of the given angle accumulates at the profile's outer radius.
"""
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

CLOSE_POINT_TOLERANCE = 1e-7
SMALL_RADIUS_TOLERANCE = 1e-9


@dataclass
class ExportItem:
    """One profile to export.

    name:  file stem, e.g. 'ring_30'
    vertices: (n, 2) unscaled profile points
    hand: +1, -1 or 0; sign applied to the helix angle for this element.
          Meshing external gears need opposite hands; an internal ring gear
          has the same hand as the planet that meshes with it.
    """
    name: str
    vertices: np.ndarray
    hand: int = 0


@dataclass
class ExportSettings:
    target_diameter_mm: float = 70.0   # scaled outer diameter of the reference profile
    thickness_mm: float = 10.0
    helix_angle_deg: float = 0.0
    gear_type: str = 'spur'            # 'spur', 'helix' or 'herringbone'
    carrier_path_points: int = 200


@dataclass
class ExportReport:
    scale_factor: float
    files: List[str] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)


def compute_scale_factor(reference_vertices: np.ndarray, target_diameter_mm: float) -> float:
    radii = np.linalg.norm(reference_vertices, axis=1)
    max_radius = float(np.max(radii)) if len(radii) else 0.0
    if max_radius <= SMALL_RADIUS_TOLERANCE:
        return 1.0
    return (target_diameter_mm / 2.0) / max_radius


def filter_close_points(points_2d: np.ndarray, tolerance: float) -> np.ndarray:
    keep = [points_2d[0]]
    for p in points_2d[1:]:
        if np.linalg.norm(p - keep[-1]) > tolerance:
            keep.append(p)
    return np.array(keep)


def rigid_twist(xy: np.ndarray, z: float, tan_helix: float, herringbone: bool, ref_radius: float) -> np.ndarray:
    """Rotate all points by the helix twist accumulated at z, and lift them to z."""
    z_for_twist = abs(z) if herringbone else z
    if abs(z) < SMALL_RADIUS_TOLERANCE or abs(tan_helix) < 1e-12 or ref_radius < SMALL_RADIUS_TOLERANCE:
        angle = 0.0
    else:
        angle = z_for_twist * tan_helix / ref_radius
    c, s = math.cos(angle), math.sin(angle)
    rot = np.array([[c, -s], [s, c]])
    xy_rot = xy @ rot.T
    return np.column_stack([xy_rot, np.full(len(xy_rot), z)])


def save_curve(points_3d: np.ndarray, filepath: str, report: ExportReport):
    if points_3d is None or len(points_3d) < 3:
        report.messages.append(f'Not enough points to save {os.path.basename(filepath)}; skipped.')
        return
    first, last = points_3d[0, :2], points_3d[-1, :2]
    tol = CLOSE_POINT_TOLERANCE * max(1.0, np.linalg.norm(first))
    if np.linalg.norm(last - first) > tol:
        points_3d = np.vstack([points_3d, points_3d[0]])
    np.savetxt(filepath, points_3d, fmt='%.8f', delimiter=' ')
    report.files.append(filepath)


def export_items(items: List[ExportItem], output_dir: str, settings: ExportSettings,
                 reference: Optional[str] = None, carrier_radius: Optional[float] = None) -> ExportReport:
    """Export all items into output_dir.

    reference: name of the item whose outer diameter is scaled to target_diameter_mm
               (defaults to the item with the largest radius).
    carrier_radius: unscaled radius of the planet-center circle; exported as
               carrier_path.txt if given.
    """
    os.makedirs(output_dir, exist_ok=True)
    items = [it for it in items if it.vertices is not None and len(it.vertices) >= 3]
    if not items:
        raise ValueError('Nothing to export: no profiles with at least 3 points.')

    if reference is None:
        ref_item = max(items, key=lambda it: np.max(np.linalg.norm(it.vertices, axis=1)))
    else:
        ref_item = next(it for it in items if it.name == reference)
    scale = compute_scale_factor(ref_item.vertices, settings.target_diameter_mm)
    report = ExportReport(scale_factor=scale)
    report.messages.append(f'Reference profile: {ref_item.name}; scale factor {scale:.6f}')

    gear_type = settings.gear_type.lower()
    herringbone = gear_type == 'herringbone'
    tan_helix = 0.0 if gear_type == 'spur' else math.tan(math.radians(settings.helix_angle_deg))

    for it in items:
        scaled = it.vertices * scale
        ref_radius = float(np.max(np.linalg.norm(scaled, axis=1)))
        pts = filter_close_points(scaled, CLOSE_POINT_TOLERANCE * scale)
        if len(pts) < 3:
            report.messages.append(f'{it.name}: fewer than 3 points after filtering; skipped.')
            continue
        hand_tan = tan_helix * it.hand
        half = settings.thickness_mm / 2.0
        for suffix, z in (('z0', 0.0), ('z_pos', +half), ('z_neg', -half)):
            curve = rigid_twist(pts, z, hand_tan, herringbone, ref_radius)
            save_curve(curve, os.path.join(output_dir, f'{it.name}_{suffix}.txt'), report)
        report.messages.append(f'{it.name}: {len(pts)} points, outer radius {ref_radius:.3f} mm')

    if carrier_radius is not None:
        r = carrier_radius * scale
        a = np.linspace(0, 2 * np.pi, settings.carrier_path_points, endpoint=True)
        carrier = np.column_stack([r * np.cos(a), r * np.sin(a), np.zeros_like(a)])
        path = os.path.join(output_dir, 'carrier_path.txt')
        np.savetxt(path, carrier, fmt='%.8f', delimiter=' ')
        report.files.append(path)
        report.messages.append(f'Carrier path radius {r:.4f} mm')

    return report


# ---------------------------------------------------------------------------
# Per gear-train-type adapters: turn a geometry object into export items.
# Each returns dict(items=[...], reference=name_or_None, carrier_radius=float_or_None)
# ---------------------------------------------------------------------------

def planetary_items(gear) -> Dict:
    ring, planet, sun, _ = gear.generate_profiles
    R, P, S = gear.G
    items = [
        ExportItem(f'ring_{R}', ring.vertices, hand=-1),
        ExportItem(f'planet_{P}', planet.vertices, hand=-1),
        ExportItem(f'sun_{S}', sun.vertices, hand=+1),
    ]
    return dict(items=items, reference=f'ring_{R}', carrier_radius=1.0)


def compound_planetary_items(gear) -> Dict:
    (r1, p1, s1, _), (r2, p2, s2, _) = gear.generate_profiles
    R1, P1, S1 = gear.G1
    R2, P2, S2 = gear.G2
    items = [
        ExportItem(f'stage1_ring_{R1}', r1.vertices, hand=-1),
        ExportItem(f'stage1_planet_{P1}', p1.vertices, hand=-1),
        ExportItem(f'stage1_sun_{S1}', s1.vertices, hand=+1),
        ExportItem(f'stage2_ring_{R2}', r2.vertices, hand=-1),
        ExportItem(f'stage2_planet_{P2}', p2.vertices, hand=-1),
        ExportItem(f'stage2_sun_{S2}', s2.vertices, hand=+1),
    ]
    # both stages share the carrier; planet centers sit at unit radius
    return dict(items=items, reference=None, carrier_radius=1.0)


def cycloid_items(gear) -> Dict:
    r, p, s, o, e = gear.generate_profiles
    items = [
        ExportItem(f'ring_pins_{gear.P + 1}', r.vertices),
        ExportItem(f'disc_{gear.P}', p.vertices),
        ExportItem('eccentric', s.vertices),
    ]
    if gear.O:
        items.append(ExportItem(f'output_pins_{gear.O}', o.vertices))
    return dict(items=items, reference=None, carrier_radius=None)


def compound_cycloid_items(gear) -> Dict:
    (r1, p1, s1, o1, e1), (r2, p2, s2, o2, e2) = gear.generate_profiles
    items = [
        ExportItem(f'stage1_ring_pins_{gear.P1 + 1}', r1.vertices),
        ExportItem(f'stage1_disc_{gear.P1}', p1.vertices),
        ExportItem(f'stage2_ring_pins_{gear.P2 + 1}', r2.vertices),
        ExportItem(f'stage2_disc_{gear.P2}', p2.vertices),
        ExportItem('eccentric', s1.vertices),
    ]
    return dict(items=items, reference=None, carrier_radius=None)


def nabtesco_items(gear) -> Dict:
    (r, p, s, o, e), (pr, pp, ps, pc) = gear.generate_profiles
    items = [
        ExportItem(f'ring_pins_{gear.L + 1}', r.vertices),
        ExportItem(f'lobed_disc_{gear.L}', p.vertices),
        ExportItem(f'wobbler_{gear.W}', pp.vertices, hand=-1),
        ExportItem(f'sun_{gear.S}', ps.vertices, hand=+1),
    ]
    return dict(items=items, reference=None, carrier_radius=None)


def simple_items(gear) -> Dict:
    a, b = gear.generate_profiles
    A = gear.geometry.get('A')
    B = gear.geometry.get('B')
    N = gear.geometry.get('N')
    if N is not None:
        items = [ExportItem(f'inner_{N}', a.vertices), ExportItem(f'outer_{N + 1}', b.vertices)]
    else:
        items = [ExportItem(f'gear_a_{A}', a.vertices, hand=+1), ExportItem(f'gear_b_{B}', b.vertices, hand=-1)]
    return dict(items=items, reference=None, carrier_radius=None)
