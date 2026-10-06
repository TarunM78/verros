"""Browser-facing API for pygeartrain, designed to run under Pyodide.

Everything crosses the JS boundary as JSON strings or flat numpy arrays so no
matplotlib is required.  A single module-level session holds the current gear.

JS usage (via pyodide.pyimport('pygeartrain.webapi')):
    api.list_specs()                       -> JSON catalogue
    api.build(spec, kin_json, params_json) -> JSON result (ratios, warnings, limit...)
    api.frame(phase)                       -> JSON {groups:[{color,start,count}]}
    api.frame_buffer()                     -> float32 (n, 2, 2) segments for the last frame
    api.angular_scene()                    -> JSON scene for the traction drive
    api.export_zip(settings_json)          -> bytes of a zip with CAD curve files
"""
import io
import json
import os
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, Dict, List

import numpy as np

from pygeartrain import cad_export
from pygeartrain.core.profiles import Profile
from pygeartrain.specs import SPECS, SPEC_BY_NAME, GearSpec

COLOR_NAMES = {'b': '#2563eb', 'r': '#dc2626', 'g': '#16a34a', 'k': '#111827'}


class _Session:
    spec: GearSpec = None
    gear = None
    kin = None
    params = None
    _frame_groups: List[Dict] = []
    _frame_data: np.ndarray = np.zeros((0, 2, 2), np.float32)


S = _Session()


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

def _spec_to_dict(spec: GearSpec) -> Dict[str, Any]:
    return {
        'name': spec.name,
        'description': spec.description,
        'members': [[k, v] for k, v in spec.members.items()],
        'default_kin': list(spec.default_kin),
        'has_fixed': spec.has_fixed,
        'params': [asdict(p) | {'choices': list(p.choices)} for p in spec.params],
        'presets': {k: dict(v, kin=list(v['kin'])) for k, v in spec.presets.items()},
        'animatable': spec.animatable,
        'exportable': spec.export is not None,
        'extra_kin': (asdict(spec.extra_kin) | {'choices': list(spec.extra_kin.choices)}) if spec.extra_kin else None,
    }


def list_specs() -> str:
    return json.dumps([_spec_to_dict(s) for s in SPECS])


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------

def _coerce_params(spec: GearSpec, raw: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for p in spec.params:
        val = raw.get(p.key, p.default)
        if p.kind == 'int':
            val = int(val)
        elif p.kind == 'float':
            val = float(val)
        elif p.kind == 'bool':
            val = bool(val)
        else:
            val = str(val)
        if p.minimum is not None and p.kind in ('int', 'float') and val < p.minimum:
            raise ValueError(f'"{p.label}" must be at least {p.minimum}.')
        out[p.key] = val
    return out


def _default_anim_scale(gear) -> float:
    rs = [abs(1 / r) for r in gear.ratios_f.values() if r]
    if not rs:
        return 0.01
    return float(np.prod(rs) ** (1 / len(rs)) / 50)


def build(spec_name: str, kin_json: str, params_json: str) -> str:
    try:
        spec = SPEC_BY_NAME[spec_name]
        kin = tuple(json.loads(kin_json))
        core = kin[:3] if spec.has_fixed else kin[:2]
        if len(set(core)) != len(core):
            raise ValueError('Input, output and fixed members must all be different.')
        params = _coerce_params(spec, json.loads(params_json))
        warnings = spec.validate(params)
        gear = spec.build(kin, params)
        ratios_f = gear.ratios_f  # forces the symbolic solve
        if spec.animatable:
            limit = float(gear.limit)
        else:
            limit = 1.3
        S.spec, S.gear, S.kin, S.params = spec, gear, kin, params
        title = str(gear).replace('\n', '  |  ')
        if len(title) > 180:  # e.g. the angular contact drive's symbolic ratio runs to hundreds of chars
            geo = ', '.join(f'{k}:{v:.3g}' if isinstance(v, float) else f'{k}:{v}' for k, v in gear.geometry.items())
            title = f'{kin[0]}/{kin[1]} = {float(gear.ratio_f):.4g}  |  {geo}'
            if len(title) > 180:
                title = title[:177] + '...'
        result = {
            'ok': True,
            'title': title,
            'input': kin[0], 'output': kin[1], 'fixed': kin[2] if spec.has_fixed and len(kin) > 2 else None,
            'symbolic': str(gear.kinematics.ratio),
            'ratio': float(gear.ratio_f),
            'ratios': {k: float(v) for k, v in ratios_f.items()},
            'warnings': warnings,
            'limit': limit,
            'anim_scale': _default_anim_scale(gear),
            'animatable': spec.animatable,
            'exportable': spec.export is not None,
        }
        return json.dumps(result, allow_nan=False)  # NaN/inf would be invalid JSON for the browser
    except Exception as e:  # surface everything to the UI
        return json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'})


# ---------------------------------------------------------------------------
# frames: capture what the library would draw, without matplotlib
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.items = []  # (color, segments (m,2,2))

    def record(self, profile, color='b', **kwargs):
        edges = profile.topology.elements[1]
        if len(edges) == 0:
            return
        self.items.append((color, profile.vertices[edges]))


@contextmanager
def _recording():
    rec = _Recorder()
    original = Profile.plot

    def fake_plot(self, *args, ax=None, color='b', **kwargs):
        rec.record(self, color=color)

    Profile.plot = fake_plot
    try:
        yield rec
    finally:
        Profile.plot = original


def frame(phase: float) -> str:
    if S.gear is None or not S.spec.animatable:
        S._frame_groups, S._frame_data = [], np.zeros((0, 2, 2), np.float32)
        return json.dumps({'groups': []})
    with _recording() as rec:
        S.gear._plot(ax=None, phase=float(phase))
    groups = []
    chunks = []
    start = 0
    # merge consecutive items of the same color to keep draw calls low
    for color, seg in rec.items:
        n = len(seg)
        css = COLOR_NAMES.get(color, color)
        if groups and groups[-1]['color'] == css:
            groups[-1]['count'] += n
        else:
            groups.append({'color': css, 'start': start, 'count': n})
        chunks.append(seg.astype(np.float32))
        start += n
    S._frame_groups = groups
    S._frame_data = np.ascontiguousarray(np.concatenate(chunks, axis=0)) if chunks else np.zeros((0, 2, 2), np.float32)
    return json.dumps({'groups': groups, 'n': int(start)})


def frame_buffer() -> np.ndarray:
    return S._frame_data


# ---------------------------------------------------------------------------
# angular contact drive: port of AngularContactGeometry.plot to a plain scene
# ---------------------------------------------------------------------------

def angular_scene() -> str:
    gear = S.gear
    if gear is None or S.spec.animatable:
        return json.dumps({'polylines': [], 'points': [], 'texts': []})
    P = gear.points
    a_deg = (gear.angles * 180 / np.pi).astype(int)
    axis = np.array([-gear.ratios_f['py'], gear.ratios_f['px']])
    axis = axis / np.linalg.norm(axis) * 1.1

    polylines = [{'color': '#16a34a', 'points': (axis[:, None] * [-1, +1]).T.tolist()}]
    points = [{'x': 0.0, 'y': 0.0, 'color': '#2563eb'}]
    points += [{'x': float(x), 'y': float(y), 'color': '#f97316'} for x, y in P.reshape(-1, 2)]
    texts = []
    for i in range(2):
        for j in range(2):
            x, y = P[i, j] * 0.8
            texts.append({'x': float(x), 'y': float(y), 'text': str(int(a_deg[i][j])), 'rotation': 0})
    for label, p, rot in (('ground', P[1, 0] * 1.1, a_deg[1, 0] - 90),
                          ('output', P[1, 1] * 1.1, a_deg[1, 1] - 90),
                          ('input', P[0, :].mean(axis=0) * 1.2, a_deg[0].mean(axis=0) + 90)):
        texts.append({'x': float(p[0]), 'y': float(p[1]), 'text': label, 'rotation': float(rot)})

    a = np.linspace(0, 2 * np.pi, 200)
    xy = np.array([np.cos(a), np.sin(a)]).T
    delta = xy.reshape(-1, 1, 2) - P.reshape(1, 4, 2)
    dist = np.linalg.norm(delta, axis=2)
    mindist = np.min(dist, axis=1)
    r = mindist ** 2 * 0.5 / 2 + 1
    ds = np.linalg.norm(P[:, 0] - P[:, 1], axis=1)
    r[np.logical_and(xy[:, 0] < 0, mindist > ds[0] / 1.9)] = np.nan
    r[np.logical_and(xy[:, 0] > 0, mindist > ds[1] / 2.1)] = np.nan
    cup = xy * r[:, None]
    # split at NaNs into separate polylines
    run = []
    for pt in cup:
        if np.isnan(pt).any():
            if len(run) > 1:
                polylines.append({'color': '#dc2626', 'points': run})
            run = []
        else:
            run.append([float(pt[0]), float(pt[1])])
    if len(run) > 1:
        polylines.append({'color': '#dc2626', 'points': run})
    polylines.append({'color': '#2563eb', 'points': xy.tolist()})
    return json.dumps({'polylines': polylines, 'points': points, 'texts': texts,
                       'xlabel': 'inner <-> outer', 'ylabel': 'bottom <-> top'})


# ---------------------------------------------------------------------------
# CAD export
# ---------------------------------------------------------------------------

def export_zip(settings_json: str) -> bytes:
    if S.gear is None or S.spec.export is None:
        raise ValueError('Nothing to export for this gear train.')
    cfg = json.loads(settings_json)
    settings = cad_export.ExportSettings(
        target_diameter_mm=float(cfg.get('target_diameter_mm', 70.0)),
        thickness_mm=float(cfg.get('thickness_mm', 10.0)),
        helix_angle_deg=float(cfg.get('helix_angle_deg', 0.0)),
        gear_type=str(cfg.get('gear_type', 'spur')),
    )
    spec = S.spec.export(S.gear)
    with tempfile.TemporaryDirectory() as tmp:
        report = cad_export.export_items(spec['items'], tmp, settings,
                                         reference=spec.get('reference'), carrier_radius=spec.get('carrier_radius'))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in report.files:
                z.write(f, os.path.basename(f))
            readme = ['pygeartrain CAD export', '', f'Gear train: {S.spec.name}', f'Kinematics: {S.kin}',
                      f'Parameters: {S.params}', f'Settings: {settings}', '', *report.messages, '',
                      'Import each *_z0 / *_z_pos / *_z_neg file with Insert > Curve > Curve Through XYZ Points',
                      'and loft between the three curves of each part. Coordinates are in mm.']
            z.writestr('README.txt', '\n'.join(readme))
    return buf.getvalue()


def solid_model(settings_json: str) -> str:
    """SolidModel description (see pygeartrain.solid_model) for a CAD kernel to turn into STEP."""
    from pygeartrain.solid_model import SolidSettings, build_solid_model
    if S.gear is None or S.spec.export is None:
        raise ValueError('This gear train has no tooth profiles to export.')
    cfg = json.loads(settings_json)
    settings = SolidSettings(
        target_diameter_mm=float(cfg.get('target_diameter_mm', 70.0)),
        thickness_mm=float(cfg.get('thickness_mm', 10.0)),
        helix_angle_deg=float(cfg.get('helix_angle_deg', 0.0)),
        gear_type=str(cfg.get('gear_type', 'spur')),
        layer_gap_mm=float(cfg.get('layer_gap_mm', 1.0)),
    )
    model = build_solid_model(S.gear, S.spec.export(S.gear), settings)
    return json.dumps(model, allow_nan=False)


def export_report(settings_json: str) -> str:
    """Messages only (no files), for previewing scale and point counts."""
    if S.gear is None or S.spec.export is None:
        return json.dumps({'messages': []})
    cfg = json.loads(settings_json)
    settings = cad_export.ExportSettings(
        target_diameter_mm=float(cfg.get('target_diameter_mm', 70.0)),
        thickness_mm=float(cfg.get('thickness_mm', 10.0)),
        helix_angle_deg=float(cfg.get('helix_angle_deg', 0.0)),
        gear_type=str(cfg.get('gear_type', 'spur')),
    )
    spec = S.spec.export(S.gear)
    with tempfile.TemporaryDirectory() as tmp:
        report = cad_export.export_items(spec['items'], tmp, settings,
                                         reference=spec.get('reference'), carrier_radius=spec.get('carrier_radius'))
        return json.dumps({'messages': report.messages, 'files': [os.path.basename(f) for f in report.files],
                           'scale': report.scale_factor})
