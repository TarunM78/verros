"""Tests for pygeartrain.cad_export: XYZ curve export for CAD import."""
import os
from pathlib import Path

import numpy as np
import pytest

from pygeartrain import cad_export
from pygeartrain.cad_export import ExportSettings, export_items, rigid_twist
from pygeartrain.planetary import Planetary, PlanetaryGeometry
from pygeartrain.specs import SPECS

REFERENCE_DIR = Path(__file__).resolve().parents[2] / 'output_herringbone'

EXPECTED_FILES = sorted(
    [f'{stem}_{suffix}.txt' for stem in ('ring_30', 'planet_12', 'sun_6') for suffix in ('z0', 'z_pos', 'z_neg')]
    + ['carrier_path.txt']
)

HERRINGBONE = ExportSettings(70.0, 10.0, 20.0, 'herringbone')


def _planetary_gear():
    return PlanetaryGeometry.create(Planetary('s', 'c', 'r'), (30, 12, 6), 3, b=0.5)


def _export_planetary(out_dir, settings):
    gear = _planetary_gear()
    spec = cad_export.planetary_items(gear)
    report = export_items(spec['items'], str(out_dir), settings,
                          reference=spec['reference'], carrier_radius=spec['carrier_radius'])
    return report


def _load(out_dir, name):
    return np.loadtxt(os.path.join(str(out_dir), name))


# ---------------------------------------------------------------------------
# planetary herringbone export
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def planetary_out(tmp_path_factory):
    out = tmp_path_factory.mktemp('planetary_herringbone')
    report = _export_planetary(out, HERRINGBONE)
    return out, report


def test_planetary_export_file_set(planetary_out):
    out, report = planetary_out
    assert sorted(os.listdir(out)) == EXPECTED_FILES
    assert sorted(os.path.basename(f) for f in report.files) == EXPECTED_FILES


def test_planetary_export_arrays_are_xyz(planetary_out):
    out, _ = planetary_out
    for name in EXPECTED_FILES:
        arr = _load(out, name)
        assert arr.ndim == 2 and arr.shape[1] == 3, name
        assert arr.shape[0] >= 3, name
        assert np.all(np.isfinite(arr)), name


def test_planetary_ring_scaled_to_target_diameter(planetary_out):
    out, report = planetary_out
    ring = _load(out, 'ring_30_z0.txt')
    max_radius = np.max(np.linalg.norm(ring[:, :2], axis=1))
    assert max_radius == pytest.approx(35.0, abs=1e-6)
    assert report.scale_factor > 0


def test_planetary_z_columns(planetary_out):
    out, _ = planetary_out
    for stem in ('ring_30', 'planet_12', 'sun_6'):
        for suffix, z in (('z0', 0.0), ('z_pos', 5.0), ('z_neg', -5.0)):
            arr = _load(out, f'{stem}_{suffix}.txt')
            assert np.allclose(arr[:, 2], z), (stem, suffix)
    carrier = _load(out, 'carrier_path.txt')
    assert np.allclose(carrier[:, 2], 0.0)


def test_planetary_curves_are_closed(planetary_out):
    out, _ = planetary_out
    for name in EXPECTED_FILES:
        arr = _load(out, name)
        assert np.allclose(arr[0], arr[-1], atol=1e-7), name


def test_planetary_twisted_curves_are_rotated_copies(planetary_out):
    out, _ = planetary_out
    z0 = _load(out, 'sun_6_z0.txt')
    r0 = np.linalg.norm(z0[:, :2], axis=1)
    for suffix in ('z_pos', 'z_neg'):
        arr = _load(out, f'sun_6_{suffix}.txt')
        assert arr.shape == z0.shape
        r = np.linalg.norm(arr[:, :2], axis=1)
        # rigid rotation about the z axis preserves radii point by point
        assert np.allclose(r, r0, atol=1e-6), suffix
        # ...but the helix twist actually moves the points
        assert not np.allclose(arr[:, :2], z0[:, :2])


def test_planetary_herringbone_matches_reference_output(planetary_out):
    out, _ = planetary_out
    assert REFERENCE_DIR.is_dir(), f'reference output missing: {REFERENCE_DIR}'
    assert sorted(p.name for p in REFERENCE_DIR.glob('*.txt')) == EXPECTED_FILES
    for name in EXPECTED_FILES:
        got = (Path(out) / name).read_text()
        expected = (REFERENCE_DIR / name).read_text()
        assert got.splitlines() == expected.splitlines(), name
        assert np.array_equal(_load(out, name), np.loadtxt(REFERENCE_DIR / name)), name


# ---------------------------------------------------------------------------
# spur export and twist helper
# ---------------------------------------------------------------------------

def test_spur_export_has_no_twist(tmp_path):
    _export_planetary(tmp_path, ExportSettings(70.0, 10.0, 20.0, 'spur'))
    for stem in ('ring_30', 'planet_12', 'sun_6'):
        z0 = _load(tmp_path, f'{stem}_z0.txt')
        for suffix, z in (('z_pos', 5.0), ('z_neg', -5.0)):
            arr = _load(tmp_path, f'{stem}_{suffix}.txt')
            assert np.array_equal(arr[:, :2], z0[:, :2]), (stem, suffix)
            assert np.all(arr[:, 2] == z)


def test_rigid_twist_zero_helix_is_identity():
    xy = np.array([[1.0, 0.0], [0.0, 2.0], [-3.0, 1.0]])
    out = rigid_twist(xy, 5.0, 0.0, False, 3.0)
    assert out.shape == (3, 3)
    assert np.array_equal(out[:, :2], xy)
    assert np.all(out[:, 2] == 5.0)
    # zero z also gives no rotation, regardless of helix
    out = rigid_twist(xy, 0.0, 0.5, False, 3.0)
    assert np.array_equal(out[:, :2], xy)
    assert np.all(out[:, 2] == 0.0)


def test_rigid_twist_herringbone_symmetric():
    xy = np.array([[1.0, 0.0], [0.0, 2.0], [-3.0, 1.0], [0.5, -0.5]])
    tan_helix = np.tan(np.radians(20.0))
    pos = rigid_twist(xy, 5.0, tan_helix, True, 3.0)
    neg = rigid_twist(xy, -5.0, tan_helix, True, 3.0)
    assert np.allclose(pos[:, :2], neg[:, :2])
    assert np.all(pos[:, 2] == 5.0) and np.all(neg[:, 2] == -5.0)
    # helical (non herringbone) twists the two sides in opposite directions
    pos_h = rigid_twist(xy, 5.0, tan_helix, False, 3.0)
    neg_h = rigid_twist(xy, -5.0, tan_helix, False, 3.0)
    assert np.allclose(pos_h[:, :2], pos[:, :2])
    assert not np.allclose(neg_h[:, :2], pos_h[:, :2])
    # rotation preserves radii
    assert np.allclose(np.linalg.norm(neg_h[:, :2], axis=1), np.linalg.norm(xy, axis=1))


# ---------------------------------------------------------------------------
# per gear-train-type adapters
# ---------------------------------------------------------------------------

EXPORTABLE_SPECS = [s for s in SPECS if s.export is not None]
ADAPTERS = {
    'Planetary': cad_export.planetary_items,
    'Compound planetary': cad_export.compound_planetary_items,
    'Cycloidal drive': cad_export.cycloid_items,
    'Compound cycloid': cad_export.compound_cycloid_items,
    'Nabtesco (RV)': cad_export.nabtesco_items,
    'Simple gear pair': cad_export.simple_items,
    'Nested (gerotor)': cad_export.simple_items,
}


def _first_preset(spec):
    preset = dict(next(iter(spec.presets.values())))
    kin = tuple(preset.pop('kin'))
    params = {p.key: preset.get(p.key, p.default) for p in spec.params}
    return kin, params


def test_every_adapter_is_covered():
    assert sorted(s.name for s in EXPORTABLE_SPECS) == sorted(ADAPTERS)
    assert all(s.export is ADAPTERS[s.name] for s in EXPORTABLE_SPECS)
    skipped = [s.name for s in SPECS if s.export is None]
    assert skipped == ['Angular contact (traction)']


@pytest.mark.parametrize('spec', EXPORTABLE_SPECS, ids=lambda s: s.name)
def test_adapter_items(spec):
    kin, params = _first_preset(spec)
    gear = spec.build(kin, params)
    result = ADAPTERS[spec.name](gear)
    assert set(result) == {'items', 'reference', 'carrier_radius'}
    items = result['items']
    assert len(items) >= 2
    names = [it.name for it in items]
    assert len(set(names)) == len(names)
    for it in items:
        assert isinstance(it.vertices, np.ndarray), it.name
        assert it.vertices.ndim == 2 and it.vertices.shape[1] == 2, it.name
        assert it.vertices.shape[0] >= 3, it.name
        assert np.all(np.isfinite(it.vertices)), it.name
        assert it.hand in (-1, 0, 1)
    if result['reference'] is not None:
        assert result['reference'] in names


@pytest.mark.parametrize('spec', EXPORTABLE_SPECS, ids=lambda s: s.name)
def test_adapter_export_round_trip(spec, tmp_path):
    kin, params = _first_preset(spec)
    gear = spec.build(kin, params)
    result = spec.export(gear)
    report = export_items(result['items'], str(tmp_path), ExportSettings(50.0, 8.0, 0.0, 'spur'),
                          reference=result['reference'], carrier_radius=result['carrier_radius'])
    n_expected = 3 * len(result['items']) + (1 if result['carrier_radius'] is not None else 0)
    assert len(report.files) == n_expected
    for f in report.files:
        arr = np.loadtxt(f)
        assert arr.ndim == 2 and arr.shape[1] == 3 and arr.shape[0] >= 3
    ref_name = result['reference']
    if ref_name is None:
        ref_name = max(result['items'], key=lambda it: np.max(np.linalg.norm(it.vertices, axis=1))).name
    ref = np.loadtxt(tmp_path / f'{ref_name}_z0.txt')
    assert np.max(np.linalg.norm(ref[:, :2], axis=1)) == pytest.approx(25.0, abs=1e-6)
