"""Tests for pygeartrain.step_export: STEP solids via CadQuery (skipped without cadquery)."""
import math
import os

import pytest

cq = pytest.importorskip('cadquery')

from pygeartrain import step_export  # noqa: E402
from pygeartrain.solid_model import SolidSettings, build_solid_model  # noqa: E402
from pygeartrain.specs import SPEC_BY_NAME  # noqa: E402

THICKNESS = 10.0
PLANETARY_PARTS = {'ring_30', 'planet_12', 'sun_6'}


def _planetary_gear():
    """Small planetary preset: Planetary('s','c','r'), G=(30,12,6), N=3, b=0.5."""
    spec = SPEC_BY_NAME['Planetary']
    preset = spec.presets['Printable 30/12/6, 3 planets (CAD script default)']
    params = {p.key: preset.get(p.key, p.default) for p in spec.params}
    assert (params['R'], params['P'], params['S'], params['N'], params['b']) == (30, 12, 6, 3, 0.5)
    return spec, spec.build(preset['kin'], params)


def _model(gear_type):
    spec, gear = _planetary_gear()
    return build_solid_model(gear, spec.export(gear), SolidSettings(70, THICKNESS, 20, gear_type))


@pytest.fixture(scope='module')
def herringbone_model():
    return _model('herringbone')


@pytest.fixture(scope='module')
def herringbone_solids(herringbone_model):
    return step_export.build_solids(herringbone_model)


@pytest.fixture(scope='module')
def helix_model():
    return _model('helix')


def _angle(v):
    return math.degrees(math.atan2(v.y, v.x))


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def _seam_offsets(shape, part, selector):
    """Angle of the outer-loop seam vertex on the selected face, relative to the
    first point of the outer loop at the mid plane."""
    p0 = part['faces'][0]['outer'][0]
    r0, a0 = math.hypot(*p0), math.degrees(math.atan2(p0[1], p0[0]))
    verts = [v.Center() for v in cq.Workplane().add(shape).faces(selector).vertices().vals()]
    on_loop = [v for v in verts if abs(math.hypot(v.x, v.y) - r0) < 1e-3]
    assert on_loop, 'no seam vertex found on the outer loop'
    return sorted(_wrap(_angle(v) - a0) for v in on_loop)


# ---------------------------------------------------------------------------
# planetary herringbone

def test_write_step_planetary(tmp_path, herringbone_model):
    files = step_export.write_step(herringbone_model, str(tmp_path))
    names = sorted(os.path.basename(f) for f in files)
    assert names == sorted([f'{n}.step' for n in PLANETARY_PARTS] + ['assembly.step'])
    for f in files:
        assert os.path.isfile(f)
        with open(f, 'rb') as fh:
            assert fh.read(12) == b'ISO-10303-21'
        assert os.path.getsize(f) > 10_000


def test_part_solids_valid_and_centered(herringbone_model, herringbone_solids):
    assert set(herringbone_solids) == PLANETARY_PARTS
    for part in herringbone_model['parts']:
        shape = herringbone_solids[part['name']]
        assert shape.isValid(), part['name']
        assert len(shape.Solids()) == 1
        bb = shape.BoundingBox()
        # mid plane at z = 0
        assert bb.zmin == pytest.approx(-THICKNESS / 2, abs=1e-3)
        assert bb.zmax == pytest.approx(THICKNESS / 2, abs=1e-3)
        assert abs(bb.xmin + bb.xmax) < 0.5 and abs(bb.ymin + bb.ymax) < 0.5


def test_ring_bounding_box_matches_outer_radius(herringbone_model, herringbone_solids):
    ring = next(p for p in herringbone_model['parts'] if p['name'] == 'ring_30')
    assert ring['internal'] and ring['outer_radius_mm'] is not None
    bb = herringbone_solids['ring_30'].BoundingBox()
    assert bb.xmax - bb.xmin == pytest.approx(2 * ring['outer_radius_mm'], abs=1e-3)
    assert bb.ymax - bb.ymin == pytest.approx(2 * ring['outer_radius_mm'], abs=1e-3)
    # the tooth loop is a hole: the ring is much lighter than a full disc
    disc = math.pi * ring['outer_radius_mm'] ** 2 * THICKNESS
    assert 0.05 * disc < herringbone_solids['ring_30'].Volume() < 0.5 * disc


def test_assembly_bounding_box(herringbone_model, herringbone_solids):
    compound = step_export.build_assembly(herringbone_model, herringbone_solids)
    assert len(compound.Solids()) == len(herringbone_model['instances']) == 5
    bb = compound.BoundingBox()
    assert bb.zmax - bb.zmin == pytest.approx(THICKNESS, abs=1e-3)
    ring = next(p for p in herringbone_model['parts'] if p['name'] == 'ring_30')
    assert bb.xmax - bb.xmin == pytest.approx(2 * ring['outer_radius_mm'], abs=1e-3)


def test_herringbone_both_faces_rotate_the_same_way(herringbone_model, herringbone_solids):
    for part in herringbone_model['parts']:
        tw = part['half_twist_deg']
        assert abs(tw) > 1.0
        shape = herringbone_solids[part['name']]
        assert _seam_offsets(shape, part, '>Z') == pytest.approx([tw], abs=1e-6)
        assert _seam_offsets(shape, part, '<Z') == pytest.approx([tw], abs=1e-6)


# ---------------------------------------------------------------------------
# twist sign

def test_cadquery_twist_sign_convention():
    """Positive twist = +z face rotated counter-clockwise viewed from +z (schema convention)."""
    wp = cq.Workplane('XY').polyline([(5, -1), (5, 1), (-5, 1), (-5, -1)]).close().twistExtrude(10, 30)
    bottom = sorted(_angle(v.Center()) for v in wp.faces('<Z').vertices().vals())
    top = sorted(_angle(v.Center()) for v in wp.faces('>Z').vertices().vals())
    assert bottom == pytest.approx([-168.69, -11.31, 11.31, 168.69], abs=0.01)
    assert top == pytest.approx([-161.31, -138.69, 18.69, 41.31], abs=0.01)   # bottom + 30 deg


def test_helix_twist_sign(helix_model):
    solids = step_export.build_solids(helix_model)
    for part in helix_model['parts']:
        tw = part['half_twist_deg']
        shape = solids[part['name']]
        assert shape.isValid(), part['name']
        assert _seam_offsets(shape, part, '>Z') == pytest.approx([tw], abs=1e-6)
        assert _seam_offsets(shape, part, '<Z') == pytest.approx([-tw], abs=1e-6)
    # sun and planets twist in opposite directions, so they can mesh
    twists = {p['name']: p['half_twist_deg'] for p in helix_model['parts']}
    assert twists['sun_6'] * twists['planet_12'] < 0


def test_spur_has_no_twist(herringbone_model):
    sun = next(p for p in herringbone_model['parts'] if p['name'] == 'sun_6')
    shape = step_export.solid_from_part(sun, THICKNESS, 'spur')
    assert shape.isValid()
    assert _seam_offsets(shape, sun, '>Z') == pytest.approx([0.0], abs=1e-9)
    assert _seam_offsets(shape, sun, '<Z') == pytest.approx([0.0], abs=1e-9)


# ---------------------------------------------------------------------------
# synthetic parts: fuse, polyline, layers, missing cadquery

def _circle(r, cx=0.0, cy=0.0, n=48, ccw=True):
    pts = [[cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)] for i in range(n)]
    return pts if ccw else pts[::-1]


def test_fused_part_is_one_solid():
    part = {'name': 'plate_pins', 'fuse': True, 'half_twist_deg': 0.0,
            'faces': [{'outer': _circle(10), 'holes': []},
                      {'outer': _circle(3, 9, 0), 'holes': []},     # overlaps the plate
                      {'outer': _circle(3, -9, 0), 'holes': []}]}
    shape = step_export.solid_from_part(part, 4.0, 'spur')
    assert shape.isValid()
    assert len(shape.Solids()) == 1
    v_plate = math.pi * 100 * 4
    v_pins = 2 * math.pi * 9 * 4
    assert v_plate < shape.Volume() < v_plate + v_pins


def test_disjoint_fused_part_keeps_all_solids():
    part = {'name': 'pins', 'fuse': True, 'half_twist_deg': 0.0,
            'faces': [{'outer': _circle(1, 5, 0), 'holes': []}, {'outer': _circle(1, -5, 0), 'holes': []}]}
    shape = step_export.solid_from_part(part, 4.0, 'spur')
    assert shape.isValid()
    assert len(shape.Solids()) == 2


@pytest.mark.parametrize('curves', ['spline', 'polyline'])
def test_hole_and_curve_modes(curves):
    part = {'name': 'washer', 'half_twist_deg': 0.0,
            'faces': [{'outer': _circle(10), 'holes': [_circle(4, ccw=False)]}]}
    shape = step_export.solid_from_part(part, 2.0, 'spur', curves=curves)
    assert shape.isValid()
    expected = math.pi * (100 - 16) * 2
    assert shape.Volume() == pytest.approx(expected, rel=0.02)


def test_invalid_curve_mode():
    with pytest.raises(ValueError):
        step_export.write_step({'thickness_mm': 1, 'gear_type': 'spur', 'parts': [], 'instances': []},
                               'unused', curves='nurbs')


def test_layers_and_placement(tmp_path):
    model = {'units': 'mm', 'thickness_mm': 4.0, 'layer_gap_mm': 1.5, 'gear_type': 'spur', 'scale_factor': 1.0,
             'parts': [{'name': 'disc', 'half_twist_deg': 0.0, 'faces': [{'outer': _circle(5), 'holes': []}]}],
             'instances': [{'part': 'disc', 'angle_deg': 0.0, 'x_mm': 0.0, 'y_mm': 0.0, 'layer': 0},
                           {'part': 'disc', 'angle_deg': 90.0, 'x_mm': 20.0, 'y_mm': 0.0, 'layer': 1}]}
    compound = step_export.build_assembly(model)
    bb = compound.BoundingBox()
    assert bb.zmin == pytest.approx(-2.0, abs=1e-6)
    assert bb.zmax == pytest.approx(4.0 + 1.5 + 2.0, abs=1e-6)     # layer 1 mid plane at 5.5
    assert bb.xmax == pytest.approx(25.0, abs=1e-6)
    files = step_export.write_step(model, str(tmp_path), parts=False)
    assert [os.path.basename(f) for f in files] == ['assembly.step']


def test_missing_cadquery_message(monkeypatch):
    monkeypatch.setattr(step_export, 'cq', None)
    with pytest.raises(RuntimeError, match='pip install cadquery'):
        step_export.write_step({'thickness_mm': 1, 'gear_type': 'spur', 'parts': [], 'instances': []}, 'unused')
    with pytest.raises(RuntimeError, match='CadQuery'):
        step_export.solid_from_part({'name': 'x', 'faces': [{'outer': _circle(1), 'holes': []}]}, 1.0, 'spur')
