"""Catalogue of gear train types for the GUIs.

Each GearSpec describes one gear train type: which members can be input,
output and fixed, which geometry parameters it takes, example presets, how to
build the pygeartrain geometry object from those, and how to turn it into CAD
export items.  Shared by the desktop (tkinter) GUI and the web interface.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pygeartrain.planetary import Planetary, PlanetaryGeometry
from pygeartrain.compound_planetary import CompoundPlanetary, CompoundPlanetaryGeometry
from pygeartrain.cycloid import Cycloid, CycloidGeometry
from pygeartrain.compound_cycloid import CompoundCycloid, CompoundCycloidGeometry
from pygeartrain.nabtesco import NabtescoKinematics, NabtescoGeometry
from pygeartrain.simple import SimpleGear, SimpleGeometry, NestedGear, NestedGeometry
from pygeartrain.angular_contact import AngularContact, AngularContactGeometry
from pygeartrain import cad_export


@dataclass
class Param:
    key: str
    label: str
    kind: str                      # 'int' | 'float' | 'choice' | 'bool'
    default: Any
    choices: Sequence[str] = ()
    minimum: Optional[float] = None


@dataclass
class GearSpec:
    name: str
    description: str
    members: Dict[str, str]        # dof symbol -> human label
    default_kin: Tuple[str, ...]   # (input, output[, fixed])
    has_fixed: bool
    params: List[Param]
    build: Callable[[Tuple[str, ...], Dict[str, Any]], Any]
    presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    export: Optional[Callable[[Any], Dict]] = None
    animatable: bool = True
    validate: Callable[[Dict[str, Any]], List[str]] = lambda p: []
    extra_kin: Optional[Param] = None  # e.g. angular contact fuse constraint


def _planetary_warnings(R, P, S, N, prefix=''):
    w = []
    if R != S + 2 * P:
        w.append(f'{prefix}R should equal S + 2P for the planets to fit (R={R}, S+2P={S + 2 * P}).')
    if N > 0 and (R + S) % N != 0:
        w.append(f'{prefix}(R + S) is not divisible by N={N}; planets cannot be equally spaced.')
    return w


def build_planetary(kin, p):
    gear = PlanetaryGeometry.create(Planetary(*kin), (p['R'], p['P'], p['S']), p['N'], b=p['b'])
    return gear


def build_compound_planetary(kin, p):
    return CompoundPlanetaryGeometry.create(
        CompoundPlanetary(*kin),
        (p['R1'], p['P1'], p['S1']), (p['R2'], p['P2'], p['S2']),
        p['N'], b1=p['b1'], b2=p['b2'], show_carrier=p['show_carrier'])


def build_cycloid(kin, p):
    gear = CycloidGeometry.create(Cycloid(*kin), p['P'], cycloid=p['cycloid'], O=p['O'])
    gear.b = p['b']
    gear.f = p['f']
    return gear


def build_compound_cycloid(kin, p):
    return CompoundCycloidGeometry.create(CompoundCycloid(*kin), P1=p['P1'], P2=p['P2'],
                                          b=p['b'], f=p['f'], cycloid=p['cycloid'])


def build_nabtesco(kin, p):
    return NabtescoGeometry.create(NabtescoKinematics(*kin), L=p['L'], S=p['S'], W=p['W'],
                                   b=p['b'], f=p['f'], N=p['N'])


def build_simple(kin, p):
    return SimpleGeometry(SimpleGear(*kin), {'A': p['A'], 'B': p['B']})


def build_nested(kin, p):
    return NestedGeometry(NestedGear(*kin), {'N': p['N']})


def build_angular(kin, p):
    return AngularContactGeometry.from_geometry(
        AngularContact(*kin), cone=p['cone'], squat=p['squat'], tilt=p['tilt'], asym=p['asym'], Dr=p['Dr'])


SPECS: List[GearSpec] = [
    GearSpec(
        name='Planetary',
        description='Classic single-stage planetary: sun, N planets, ring, carrier. Cycloidal tooth profile.',
        members={'s': 'sun', 'c': 'carrier', 'r': 'ring'},
        default_kin=('s', 'c', 'r'), has_fixed=True,
        params=[
            Param('R', 'Ring teeth (R)', 'int', 30, minimum=3),
            Param('P', 'Planet teeth (P)', 'int', 12, minimum=1),
            Param('S', 'Sun teeth (S)', 'int', 6, minimum=1),
            Param('N', 'Number of planets (N)', 'int', 3, minimum=1),
            Param('b', 'Epi/hypo mix b (0..1)', 'float', 0.5, minimum=0),
        ],
        build=build_planetary, export=cad_export.planetary_items,
        validate=lambda p: _planetary_warnings(p['R'], p['P'], p['S'], p['N']),
        presets={
            'Printable 30/12/6, 3 planets (CAD script default)': dict(kin=('s', 'c', 'r'), R=30, P=12, S=6, N=3, b=0.5),
            '14/4/6, 5 planets, carrier in / ring out': dict(kin=('c', 'r', 's'), R=14, P=4, S=6, N=5, b=0.8),
            '11/2/7, 6 planets': dict(kin=('s', 'c', 'r'), R=11, P=2, S=7, N=6, b=0.6),
        },
    ),
    GearSpec(
        name='Compound planetary',
        description='Two stacked planetaries sharing planets and carrier (Wolfrom). Very high ratios possible.',
        members={'s1': 'sun 1', 's2': 'sun 2', 'r1': 'ring 1', 'r2': 'ring 2', 'c': 'carrier', 'p': 'planets'},
        default_kin=('s1', 'r2', 'r1'), has_fixed=True,
        params=[
            Param('R1', 'Stage 1 ring teeth (R1)', 'int', 22, minimum=3),
            Param('P1', 'Stage 1 planet teeth (P1)', 'int', 7, minimum=1),
            Param('S1', 'Stage 1 sun teeth (S1)', 'int', 8, minimum=1),
            Param('R2', 'Stage 2 ring teeth (R2)', 'int', 21, minimum=3),
            Param('P2', 'Stage 2 planet teeth (P2)', 'int', 6, minimum=1),
            Param('S2', 'Stage 2 sun teeth (S2)', 'int', 9, minimum=1),
            Param('N', 'Number of planets (N)', 'int', 5, minimum=1),
            Param('b1', 'Stage 1 epi/hypo mix b1', 'float', 0.4, minimum=0),
            Param('b2', 'Stage 2 epi/hypo mix b2', 'float', 0.6, minimum=0),
            Param('show_carrier', 'Draw carrier', 'bool', False),
        ],
        build=build_compound_planetary, export=cad_export.compound_planetary_items,
        validate=lambda p: (_planetary_warnings(p['R1'], p['P1'], p['S1'], p['N'], 'Stage 1: ')
                            + _planetary_warnings(p['R2'], p['P2'], p['S2'], p['N'], 'Stage 2: ')),
        presets={
            'README example 22/7/8 + 21/6/9, 5 planets': dict(kin=('s1', 'r2', 'r1'), R1=22, P1=7, S1=8, R2=21, P2=6, S2=9, N=5, b1=0.4, b2=0.6),
            'Tiny 5/2/1 + 4/1/2, 3 planets': dict(kin=('s1', 'r2', 'r1'), R1=5, P1=2, S1=1, R2=4, P2=1, S2=2, N=3, b1=0.25, b2=0.75),
            'Low ratio 20/4/12 + 16/4/8, 8 planets': dict(kin=('s1', 'r2', 'r1'), R1=20, P1=4, S1=12, R2=16, P2=4, S2=8, N=8, b1=0.7, b2=0.3),
            '~50:1  13/4/5 + 21/6/9, 6 planets': dict(kin=('s1', 'r2', 'r1'), R1=13, P1=4, S1=5, R2=21, P2=6, S2=9, N=6, b1=0.33, b2=0.66),
            'Extreme ratio 55/21/13 + 42/16/10, 4 planets': dict(kin=('s1', 'r2', 'r1'), R1=55, P1=21, S1=13, R2=42, P2=16, S2=10, N=4, b1=0.55, b2=0.45),
            'Printable high ratio 43/8/27 + 37/7/23, 10 planets': dict(kin=('s1', 'r2', 'r1'), R1=43, P1=8, S1=27, R2=37, P2=7, S2=23, N=10, b1=0.4, b2=0.4),
            'Carrier driven 15/5/5 + 14/4/6, 5 planets': dict(kin=('c', 'r2', 'r1'), R1=15, P1=5, S1=5, R2=14, P2=4, S2=6, N=5, b1=0.4, b2=0.7, show_carrier=True),
        },
    ),
    GearSpec(
        name='Cycloidal drive',
        description='Eccentric-driven cycloidal disc against a pin ring, optional output pins.',
        members={'c': 'eccentric (carrier)', 'p': 'disc', 'r': 'pin ring'},
        default_kin=('c', 'p', 'r'), has_fixed=True,
        params=[
            Param('P', 'Disc lobes (P)', 'int', 9, minimum=2),
            Param('cycloid', 'Profile', 'choice', 'epi', choices=('epi', 'hypo')),
            Param('O', 'Output pins (0 = none)', 'int', 6, minimum=0),
            Param('b', 'Pin / bearing size b', 'float', 1.0, minimum=0),
            Param('f', 'Cycloid depth f (0..1)', 'float', 0.8, minimum=0),
        ],
        build=build_cycloid, export=cad_export.cycloid_items,
        presets={
            '9 lobes, epi, 6 output pins': dict(kin=('c', 'p', 'r'), P=9, cycloid='epi', O=6, b=1.0, f=0.8),
            '4 lobes, epi': dict(kin=('c', 'p', 'r'), P=4, cycloid='epi', O=0, b=1.0, f=0.8),
            '4 lobes, hypo': dict(kin=('c', 'p', 'r'), P=4, cycloid='hypo', O=0, b=1.0, f=0.8),
        },
    ),
    GearSpec(
        name='Compound cycloid',
        description='Two cycloidal stages back to back on one disc, sharing eccentricity.',
        members={'c': 'eccentric (carrier)', 'p': 'disc', 'r1': 'pin ring 1', 'r2': 'pin ring 2'},
        default_kin=('c', 'r2', 'r1'), has_fixed=True,
        params=[
            Param('P1', 'Stage 1 lobes (P1)', 'int', 8, minimum=2),
            Param('P2', 'Stage 2 lobes (P2)', 'int', 7, minimum=2),
            Param('cycloid', 'Profile', 'choice', 'epi', choices=('epi', 'hypo')),
            Param('b', 'Pin / bearing size b', 'float', 1.2, minimum=0),
            Param('f', 'Cycloid depth f (0..1)', 'float', 0.5, minimum=0),
        ],
        build=build_compound_cycloid, export=cad_export.compound_cycloid_items,
        presets={
            '8 + 7 lobes, epi': dict(kin=('c', 'r2', 'r1'), P1=8, P2=7, cycloid='epi', b=1.2, f=0.5),
            '6 + 7 lobes, hypo': dict(kin=('c', 'r2', 'r1'), P1=6, P2=7, cycloid='hypo', b=3.5, f=0.7),
            'README 3 + 4 lobes': dict(kin=('c', 'r2', 'r1'), P1=3, P2=4, cycloid='epi', b=1.0, f=1.0),
        },
    ),
    GearSpec(
        name='Nabtesco (RV)',
        description='Sun-driven wobblers move a cycloidal lobed disc inside a pin ring.',
        members={'s': 'sun', 'o': 'output carrier', 'r': 'pin ring', 'w': 'wobblers', 'l': 'lobed disc'},
        default_kin=('s', 'o', 'r'), has_fixed=True,
        params=[
            Param('L', 'Disc lobes (L)', 'int', 15, minimum=2),
            Param('S', 'Sun teeth (S)', 'int', 8, minimum=1),
            Param('W', 'Wobbler teeth (W)', 'int', 19, minimum=1),
            Param('N', 'Number of wobblers (N)', 'int', 3, minimum=1),
            Param('b', 'Pin size b', 'float', 1.5, minimum=0),
            Param('f', 'Cycloid depth f (0..1)', 'float', 0.8, minimum=0),
        ],
        build=build_nabtesco, export=cad_export.nabtesco_items,
        presets={
            'L15 S8 W19, 3 wobblers': dict(kin=('s', 'o', 'r'), L=15, S=8, W=19, N=3, b=1.5, f=0.8),
            'L15 S10 W16, 4 wobblers': dict(kin=('s', 'o', 'r'), L=15, S=10, W=16, N=4, b=1.5, f=0.9),
        },
    ),
    GearSpec(
        name='Simple gear pair',
        description='Two external gears in mesh.',
        members={'a': 'gear A', 'b': 'gear B'},
        default_kin=('a', 'b'), has_fixed=False,
        params=[
            Param('A', 'Gear A teeth', 'int', 4, minimum=1),
            Param('B', 'Gear B teeth', 'int', 5, minimum=1),
        ],
        build=build_simple, export=cad_export.simple_items,
        presets={'4 : 5': dict(kin=('a', 'b'), A=4, B=5), '7 : 20': dict(kin=('a', 'b'), A=7, B=20)},
    ),
    GearSpec(
        name='Nested (gerotor)',
        description='Inner N-lobe rotor inside an N+1 lobe outer, as in a progressive cavity pump.',
        members={'a': 'inner rotor', 'b': 'outer rotor'},
        default_kin=('a', 'b'), has_fixed=False,
        params=[Param('N', 'Inner lobes (N)', 'int', 4, minimum=2)],
        build=build_nested, export=cad_export.simple_items,
        presets={'4 lobes': dict(kin=('a', 'b'), N=4), '7 lobes': dict(kin=('a', 'b'), N=7)},
    ),
    GearSpec(
        name='Angular contact (traction)',
        description='4-point angular contact ball traction drive. Ratio only; no tooth profiles.',
        members={'rib': 'inner ring bottom', 'rit': 'inner ring top', 'rob': 'outer ring bottom',
                 'rot': 'outer ring top', 'c': 'ball cage'},
        default_kin=('rib', 'rot', 'rob'), has_fixed=True,
        extra_kin=Param('fuse', 'Fused rings', 'choice', 'rib-rit', choices=('rib-rit', 'rob-rot')),
        params=[
            Param('cone', 'Cone angle (deg)', 'float', 5.0),
            Param('squat', 'Squat angle (deg)', 'float', 10.0),
            Param('tilt', 'Tilt angle (deg)', 'float', 5.0),
            Param('asym', 'Asymmetry (deg)', 'float', 0.0),
            Param('Dr', 'Ring / ball diameter ratio', 'float', 3.0, minimum=0.01),
        ],
        build=build_angular, export=None, animatable=False,
        presets={
            'High ratio, fused inner ring driven': dict(kin=('rib', 'rot', 'rob'), fuse='rib-rit', cone=5, squat=10, tilt=5, asym=0, Dr=3),
            'Low ratio (Eviolo style), outer driven': dict(kin=('rob', 'rot', 'rib'), fuse='rib-rit', cone=5, squat=10, tilt=0, asym=0, Dr=3),
        },
    ),
]
SPEC_BY_NAME = {s.name: s for s in SPECS}


