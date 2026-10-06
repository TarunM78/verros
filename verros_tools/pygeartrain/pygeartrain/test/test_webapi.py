"""Tests for pygeartrain.webapi: the JSON / numpy facade used by the browser UI."""
import io
import json
import zipfile

import numpy as np
import pytest

from pygeartrain import webapi as api

PLANETARY_KIN = '["s","c","r"]'
PLANETARY_PARAMS = '{"R":30,"P":12,"S":6,"N":3,"b":0.5}'

ANGULAR_KIN = '["rib","rot","rob","rib-rit"]'
ANGULAR_PARAMS = '{"cone":5,"squat":10,"tilt":5,"asym":0,"Dr":3}'

EXPORT_SETTINGS = '{"target_diameter_mm":70,"thickness_mm":10,"helix_angle_deg":20,"gear_type":"herringbone"}'
CURVE_FILES = sorted(
    [f'{stem}_{suffix}.txt' for stem in ('ring_30', 'planet_12', 'sun_6') for suffix in ('z0', 'z_pos', 'z_neg')]
    + ['carrier_path.txt']
)


def _build(spec, kin, params):
    result = json.loads(api.build(spec, kin, params))
    assert result['ok'], result.get('error')
    return result


def _build_planetary():
    return _build('Planetary', PLANETARY_KIN, PLANETARY_PARAMS)


def _build_angular():
    return _build('Angular contact (traction)', ANGULAR_KIN, ANGULAR_PARAMS)


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

def test_list_specs():
    specs = json.loads(api.list_specs())
    assert isinstance(specs, list)
    assert len(specs) == 8
    for spec in specs:
        assert {'name', 'members', 'params', 'presets'} <= set(spec)
        assert isinstance(spec['name'], str) and spec['name']
        assert isinstance(spec['members'], list) and spec['members']
        assert isinstance(spec['params'], list)
        assert isinstance(spec['presets'], dict) and spec['presets']
        for preset in spec['presets'].values():
            assert isinstance(preset['kin'], list)
    names = [s['name'] for s in specs]
    assert len(set(names)) == 8
    assert 'Planetary' in names and 'Angular contact (traction)' in names
    # round trip: the catalogue must be JSON serialisable all the way down
    json.dumps(specs)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def test_build_planetary():
    result = _build_planetary()
    assert result['ok'] is True
    assert result['ratio'] == pytest.approx(6.0)
    assert result['symbolic'] == '(R + S)/S'
    assert {'s', 'c', 'r', 'p'} <= set(result['ratios'])
    assert result['ratios']['s'] == pytest.approx(6.0)
    assert result['ratios']['c'] == pytest.approx(1.0)
    assert result['ratios']['r'] == pytest.approx(0.0)
    assert result['limit'] > 0
    assert result['warnings'] == []
    assert result['input'] == 's' and result['output'] == 'c' and result['fixed'] == 'r'
    assert result['animatable'] is True and result['exportable'] is True
    assert isinstance(result['title'], str) and result['title']


def test_build_duplicate_members_fails():
    result = json.loads(api.build('Planetary', '["s","s","r"]', PLANETARY_PARAMS))
    assert result['ok'] is False
    assert 'different' in result['error']


def test_build_mismatched_ring_warns():
    result = _build('Planetary', PLANETARY_KIN, '{"R":31,"P":12,"S":6,"N":3,"b":0.5}')
    assert result['ok'] is True
    assert isinstance(result['warnings'], list)
    assert len(result['warnings']) > 0
    assert any('S + 2P' in w for w in result['warnings'])


def test_build_unknown_spec_fails_gracefully():
    result = json.loads(api.build('Not a gear', '["a","b"]', '{}'))
    assert result['ok'] is False
    assert 'error' in result


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------

def test_frame_planetary():
    _build_planetary()
    f = json.loads(api.frame(0.3))
    assert f['groups'], 'expected at least one colour group'
    n = f['n']
    assert n > 0
    assert sum(g['count'] for g in f['groups']) == n
    # groups are contiguous and ordered
    start = 0
    for g in f['groups']:
        assert g['start'] == start
        assert g['count'] > 0
        assert g['color'].startswith('#')
        start += g['count']
    buf = api.frame_buffer()
    assert isinstance(buf, np.ndarray)
    assert buf.dtype == np.float32
    assert buf.shape == (n, 2, 2)
    assert buf.flags['C_CONTIGUOUS']
    assert np.all(np.isfinite(buf))


def test_frame_changes_with_phase():
    _build_planetary()
    api.frame(0.0)
    a = api.frame_buffer().copy()
    api.frame(0.3)
    b = api.frame_buffer()
    assert a.shape == b.shape
    assert not np.array_equal(a, b)


def test_frame_compound_planetary_colour_groups():
    params = json.dumps(dict(R1=22, P1=7, S1=8, R2=21, P2=6, S2=9, N=5, b1=0.4, b2=0.6, show_carrier=False))
    _build('Compound planetary', '["s1","r2","r1"]', params)
    f = json.loads(api.frame(0.3))
    assert [g['color'] for g in f['groups']] == ['#dc2626', '#2563eb']
    assert sum(g['count'] for g in f['groups']) == f['n']
    assert api.frame_buffer().shape == (f['n'], 2, 2)


def test_frame_for_non_animatable_spec_is_empty():
    _build_angular()
    f = json.loads(api.frame(0.3))
    assert f == {'groups': []}
    assert api.frame_buffer().shape == (0, 2, 2)


# ---------------------------------------------------------------------------
# angular contact scene
# ---------------------------------------------------------------------------

def test_angular_scene():
    result = _build_angular()
    assert result['ratio'] == pytest.approx(14.724, abs=1e-3)
    assert result['animatable'] is False and result['exportable'] is False
    scene = json.loads(api.angular_scene())
    assert scene['polylines'], 'expected polylines'
    assert len(scene['points']) == 5
    assert len(scene['texts']) == 7
    for pl in scene['polylines']:
        assert pl['color'].startswith('#')
        assert len(pl['points']) >= 2
        assert all(len(p) == 2 for p in pl['points'])
    for p in scene['points']:
        assert {'x', 'y', 'color'} <= set(p)
    labels = [t['text'] for t in scene['texts']]
    assert {'ground', 'output', 'input'} <= set(labels)
    assert all({'x', 'y', 'text', 'rotation'} <= set(t) for t in scene['texts'])


def test_angular_scene_empty_for_animatable_spec():
    _build_planetary()
    scene = json.loads(api.angular_scene())
    assert scene == {'polylines': [], 'points': [], 'texts': []}


def test_angular_title_is_capped():
    result = _build_angular()
    assert len(result['title']) <= 180


# ---------------------------------------------------------------------------
# CAD export
# ---------------------------------------------------------------------------

def test_export_zip_planetary():
    _build_planetary()
    data = api.export_zip(EXPORT_SETTINGS)
    assert isinstance(data, (bytes, bytearray))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert 'README.txt' in names
        assert sorted(n for n in names if n != 'README.txt') == CURVE_FILES
        readme = z.read('README.txt').decode()
        assert 'Planetary' in readme
        assert 'herringbone' in readme
        ring = np.loadtxt(io.StringIO(z.read('ring_30_z0.txt').decode()))
        assert ring.ndim == 2 and ring.shape[1] == 3
        assert np.max(np.linalg.norm(ring[:, :2], axis=1)) == pytest.approx(35.0, abs=1e-6)
        z_pos = np.loadtxt(io.StringIO(z.read('ring_30_z_pos.txt').decode()))
        assert np.allclose(z_pos[:, 2], 5.0)


def test_export_report_planetary():
    _build_planetary()
    report = json.loads(api.export_report(EXPORT_SETTINGS))
    assert sorted(report['files']) == CURVE_FILES
    assert report['scale'] > 0
    assert report['messages']


def test_export_zip_rejects_angular_contact():
    _build_angular()
    with pytest.raises(ValueError):
        api.export_zip(EXPORT_SETTINGS)
    assert json.loads(api.export_report(EXPORT_SETTINGS)) == {'messages': []}
