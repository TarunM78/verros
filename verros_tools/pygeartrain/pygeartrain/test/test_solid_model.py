import json
import os
import re
import math

import numpy as np
import pytest

from pygeartrain import cad_export
from pygeartrain.core.profiles import Profile, circle
from pygeartrain.planetary import Planetary, PlanetaryGeometry
from pygeartrain.cycloid import Cycloid, CycloidGeometry
from pygeartrain.compound_planetary import CompoundPlanetary, CompoundPlanetaryGeometry
from pygeartrain.solid_model import (SolidSettings, build_solid_model, nest_loops, profile_loops,
                                     rigid_fit, signed_area)


def test_profile_loops_splits_concatenated_circles():
    p = Profile.concat([circle(2.0, N=50), circle(0.5, N=20).translate([0.3, 0.0])])
    loops = profile_loops(p)
    # circle() repeats its first point at the end; the duplicate closing point is dropped
    assert sorted(len(l) for l in loops) == [19, 49]
    for l in loops:
        assert l.shape[1] == 2 and np.linalg.norm(l[0] - l[-1]) > 1e-9  # not closed twice


def test_nest_loops_outer_hole_island():
    def ring(r, n=40, c=(0, 0)):
        a = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return np.column_stack([c[0] + r * np.cos(a), c[1] + r * np.sin(a)])
    faces = nest_loops([ring(1.0), ring(3.0), ring(0.2), ring(1.0, c=(10, 0))])
    # big disc with a hole r=1, inside which an island r=0.2 is its own face; plus a separate disc
    outers = sorted(abs(signed_area(f['outer'])) for f in faces)
    assert len(faces) == 3
    assert [len(f['holes']) for f in sorted(faces, key=lambda f: -abs(signed_area(f['outer'])))] == [1, 0, 0]
    for f in faces:
        assert signed_area(f['outer']) > 0
        for h in f['holes']:
            assert signed_area(h) < 0
    assert outers[-1] == pytest.approx(math.pi * 9, rel=0.02)


def test_rigid_fit_recovers_rotation_and_translation():
    rng = np.random.default_rng(1)
    src = rng.normal(size=(30, 2))
    a, t = 0.7, np.array([3.0, -2.0])
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    dst = src @ R.T + t
    angle, tt, rms = rigid_fit(src, dst)
    assert angle == pytest.approx(a, abs=1e-12)
    assert np.allclose(tt, t)
    assert rms < 1e-12


def test_planetary_model_parts_and_instances():
    gear = PlanetaryGeometry.create(Planetary('s', 'c', 'r'), (30, 12, 6), 3, b=0.5)
    model = build_solid_model(gear, cad_export.planetary_items(gear), SolidSettings(70, 10, 20, 'herringbone'))
    json.dumps(model)  # serialisable
    names = [p['name'] for p in model['parts']]
    assert names == ['ring_30', 'planet_12', 'sun_6']
    ring = model['parts'][0]
    assert ring['internal'] and not ring['fuse']
    assert ring['ref_radius_mm'] == pytest.approx(35.0, abs=1e-6)
    assert ring['outer_radius_mm'] == pytest.approx(35.0 * 1.12, abs=1e-6)
    assert len(ring['faces']) == 1 and len(ring['faces'][0]['holes']) == 1
    # outer loop of the ring is the boundary circle, CCW; the tooth loop is the hole, CW
    outer = np.array(ring['faces'][0]['outer'])
    assert signed_area(outer) > 0
    assert np.allclose(np.linalg.norm(outer, axis=1), ring['outer_radius_mm'])
    assert signed_area(np.array(ring['faces'][0]['holes'][0])) < 0
    # helix hands: sun opposite to planet and ring; twist = (t/2) tan(helix) / r
    sun, planet = model['parts'][2], model['parts'][1]
    assert sun['half_twist_deg'] > 0 > planet['half_twist_deg']
    assert sun['half_twist_deg'] == pytest.approx(math.degrees(5 * math.tan(math.radians(20)) / sun['ref_radius_mm']))
    # instances: 1 ring, 3 planets at the carrier radius, 1 sun
    inst = model['instances']
    assert [i['part'] for i in inst].count('planet_12') == 3
    carrier_r = model['scale_factor'] * 1.0
    for i in inst:
        if i['part'] == 'planet_12':
            assert math.hypot(i['x_mm'], i['y_mm']) == pytest.approx(carrier_r, abs=1e-6)
        else:
            assert math.hypot(i['x_mm'], i['y_mm']) < 1e-6
        assert i['layer'] == 0


def test_spur_has_no_twist():
    gear = PlanetaryGeometry.create(Planetary('s', 'c', 'r'), (30, 12, 6), 3, b=0.5)
    model = build_solid_model(gear, cad_export.planetary_items(gear), SolidSettings(70, 10, 20, 'spur'))
    assert all(p['half_twist_deg'] == 0 for p in model['parts'])


def test_compound_planetary_layers():
    kin = CompoundPlanetary('s1', 'r2', 'r1')
    gear = CompoundPlanetaryGeometry.create(kin, (5, 2, 1), (4, 1, 2), 3, b1=0.25, b2=0.75)
    model = build_solid_model(gear, cad_export.compound_planetary_items(gear), SolidSettings(40, 6, 0, 'spur', layer_gap_mm=2))
    layers = {i['part']: i['layer'] for i in model['instances']}
    assert all(layers[n] == 0 for n in layers if n.startswith('stage1'))
    assert all(layers[n] == 1 for n in layers if n.startswith('stage2'))
    assert model['layer_gap_mm'] == 2 and model['thickness_mm'] == 6


def test_cycloid_pins_are_fused_solids_and_disc_has_holes():
    gear = CycloidGeometry.create(Cycloid('c', 'p', 'r'), 9, cycloid='epi', O=6)
    model = build_solid_model(gear, cad_export.cycloid_items(gear), SolidSettings(60, 8, 0, 'spur'))
    parts = {p['name']: p for p in model['parts']}
    pins = parts['ring_pins_10']
    assert pins['fuse'] and not pins['internal']
    assert len(pins['faces']) == 10 and all(not f['holes'] for f in pins['faces'])
    disc = parts['disc_9']
    assert not disc['fuse'] and len(disc['faces']) == 1 and len(disc['faces'][0]['holes']) == 6
    # the shaft circle sits tangent inside the cam circle: as a solid it is just the cam
    ecc = parts['eccentric']
    assert ecc['fuse'] and len(ecc['faces']) == 1 and not ecc['faces'][0]['holes']
    assert np.max(np.linalg.norm(np.array(ecc['faces'][0]['outer']), axis=1)) == pytest.approx(ecc['ref_radius_mm'])
    # carrier plate with pins inside it: the pins add nothing to the 2D extrusion
    carrier = parts['output_pins_6']
    assert carrier['fuse'] and len(carrier['faces']) == 1


def test_webapi_solid_model_roundtrip():
    from pygeartrain import webapi as api
    r = json.loads(api.build('Planetary', json.dumps(['s', 'c', 'r']), json.dumps({'R': 30, 'P': 12, 'S': 6, 'N': 3, 'b': 0.5})))
    assert r['ok']
    model = json.loads(api.solid_model(json.dumps({'target_diameter_mm': 70, 'thickness_mm': 10, 'helix_angle_deg': 20,
                                                   'gear_type': 'helix', 'layer_gap_mm': 2.5})))
    assert [p['name'] for p in model['parts']] == ['ring_30', 'planet_12', 'sun_6']
    assert model['gear_type'] == 'helix' and model['layer_gap_mm'] == 2.5
    assert len(model['instances']) == 5
    # no solids for the traction drive
    api.build('Angular contact (traction)', json.dumps(['rib', 'rot', 'rob', 'rib-rit']),
              json.dumps({'cone': 5, 'squat': 10, 'tilt': 5, 'asym': 0, 'Dr': 3}))
    with pytest.raises(ValueError):
        api.solid_model('{}')


def test_dimensions_planetary_centre_circle():
    from pygeartrain.solid_model import dimensions
    gear = PlanetaryGeometry.create(Planetary('s', 'c', 'r'), (30, 12, 6), 3, b=0.5)
    d = dimensions(gear, cad_export.planetary_items(gear), 70.0)
    rows = {r['part']: r for r in d['rows']}
    assert rows['ring_30']['outer_diameter_mm'] == pytest.approx(70.0)
    # planet centres sit at unit radius in the library's units
    assert rows['planet_12']['count'] == 3
    assert rows['planet_12']['center_circle_diameter_mm'] == pytest.approx(2 * d['scale_factor'])
    assert d['headline']['label'].startswith('Planet centre circle')
    assert d['headline']['value_mm'] == pytest.approx(2 * d['scale_factor'])
    # scales linearly with the requested diameter
    d2 = dimensions(gear, cad_export.planetary_items(gear), 140.0)
    assert d2['headline']['value_mm'] == pytest.approx(2 * d['headline']['value_mm'])


def test_dimensions_gear_pair_centre_distance():
    from pygeartrain.simple import SimpleGear, SimpleGeometry
    from pygeartrain.solid_model import dimensions
    gear = SimpleGeometry(SimpleGear('a', 'b'), {'A': 4, 'B': 5})
    d = dimensions(gear, cad_export.simple_items(gear), 70.0)
    rows = {r['part']: r for r in d['rows']}
    assert d['headline']['label'].startswith('Centre distance')
    assert d['headline']['value_mm'] == pytest.approx(rows['gear_a_4']['center_offset_mm'] + rows['gear_b_5']['center_offset_mm'])


def test_webapi_dimensions():
    from pygeartrain import webapi as api
    api.build('Planetary', json.dumps(['s', 'c', 'r']), json.dumps({'R': 30, 'P': 12, 'S': 6, 'N': 3, 'b': 0.5}))
    d = json.loads(api.dimensions(json.dumps({'target_diameter_mm': 100})))
    assert d['headline']['value_mm'] == pytest.approx(2 * d['scale_factor'])
    assert {r['part'] for r in d['rows']} == {'ring_30', 'planet_12', 'sun_6'}


def test_featurescript_generation():
    from pygeartrain.fs_export import generate_featurescript, builder_source, MAX_POINTS_PER_LOOP
    gear = PlanetaryGeometry.create(Planetary('s', 'c', 'r'), (30, 12, 6), 3, b=0.5)
    model = build_solid_model(gear, cad_export.planetary_items(gear), SolidSettings(70, 10, 20, 'herringbone'))
    code = generate_featurescript(model, title='Planetary 30/12/6')
    assert code.startswith('FeatureScript 1948;')
    assert code.count('{') == code.count('}') and code.count('[') == code.count(']') and code.count('(') == code.count(')')
    for name in ('FACES_RING_30', 'FACES_PLANET_12', 'FACES_SUN_6', 'export const verrosGearTrain', 'function buildGearTrain', 'export enum ToothType'):
        assert name in code
    assert '"Default" : "HERRINGBONE"' in code
    assert code.count('"part" : "planet_12"') == 3
    # every embedded loop is capped
    for m in re.finditer(r'"outer" : \[(.*?)\], "holes"', code):
        assert m.group(1).count('], [') + 1 <= MAX_POINTS_PER_LOOP
    # the builder section comes verbatim from the parametric feature
    assert builder_source() in code
    # the parametric file itself is balanced and self-contained
    src = open(os.path.join(os.path.dirname(cad_export.__file__), 'onshape', 'cycloidalPlanetary.fs'), encoding='utf-8').read()
    assert src.count('{') == src.count('}') and src.count('(') == src.count(')')
    assert 'export const cycloidalPlanetary' in src


def test_webapi_featurescript():
    from pygeartrain import webapi as api
    api.build('Cycloidal drive', json.dumps(['c', 'p', 'r']), json.dumps({'P': 9, 'cycloid': 'epi', 'O': 6, 'b': 1.0, 'f': 0.8}))
    code = api.featurescript(json.dumps({'target_diameter_mm': 60, 'thickness_mm': 8, 'gear_type': 'spur'}))
    assert 'FACES_RING_PINS_10' in code and 'FACES_DISC_9' in code and '"fuse" : true' in code
