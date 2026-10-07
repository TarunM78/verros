# verros

Gear train design tools built on [pygeartrain](https://github.com/CKraft11/pygeartrain):
a browser app (GitHub Pages), a desktop GUI, and the patched library they share.

```
verros/
├── verros_tools/
│   ├── web/                 Browser app. Runs the real Python library in Pyodide.
│   │   ├── index.html, app.js, worker.js, style.css
│   │   ├── step/            OpenCascade (replicad) worker that builds STEP solids
│   │   └── build_bundle.py  Packs pygeartrain + pure-Python deps into web/dist/bundle.zip
│   └── pygeartrain/         Library (fork of CKraft11/pygeartrain, see pygeartrain/UPSTREAM.md)
│       ├── pygeartrain/     package: gear trains, profiles, kinematics
│       │   ├── cad_export.py   SolidWorks XYZ curve export for every gear type
│       │   ├── solid_model.py  faces, holes, twist and placements for a CAD kernel
│       │   ├── step_export.py  STEP solids via CadQuery (desktop, optional)
│       │   ├── specs.py        catalogue of gear types, parameters and presets (shared by both GUIs)
│       │   └── webapi.py       JSON/array API used by the web app
│       ├── pygeartrain_gui.py  desktop (tkinter + matplotlib) GUI
│       └── requirements.txt    pip setup (no conda needed)
└── .github/workflows/pages.yml   builds the bundle, runs tests, deploys the web app to Pages
```

## Web app

Open the GitHub Pages site for this repository. Everything runs client-side:
the first visit downloads about 40 MB of Python packages (numpy, scipy, sympy,
shapely), which the browser then caches.

Features: all gear train types (planetary, compound planetary, cycloidal,
compound cycloid, Nabtesco, simple pair, gerotor, angular-contact traction),
choice of input / output / fixed member, symbolic and numeric ratios, assembly
warnings, animation with speed and phase control, PNG and WebM download, a zip
of CAD point curves for SolidWorks, and direct **STEP export** of 3D solids.
Designs are encoded in the URL hash so a link reproduces them.

### STEP export

The "STEP solids" card builds real B-rep solids in the browser with
OpenCascade (via [replicad](https://replicad.xyz)) and downloads a zip with one
`<part>.step` per gear, centred on its own axis with the mid-plane at Z=0, plus
`assembly.step` with every part placed as in the animation. Spur, helical and
herringbone teeth are supported; ring gears get an outer boundary circle, pin
rings become solid pins, and discs keep their bearing holes. The geometry comes
from `pygeartrain/solid_model.py`, which turns the 2D profiles into faces with
holes plus placements; the same description drives the desktop exporter.

### Onshape

Two ways to get a design into Onshape, both verified against Onshape's own
FeatureScript engine:

- **Parametric feature** `verros_tools/pygeartrain/pygeartrain/onshape/cycloidalPlanetary.fs`:
  paste into a Feature Studio and the "Cycloidal planetary" feature appears in
  the Part Studio toolbar. Live parameters: tooth counts of ring, planets and
  sun (and of a second stage), number of planets, epi/hypo mix, size by module,
  planet-centre circle or ring tooth diameter, an explicit ring outer diameter,
  thickness, spur / helical / herringbone with helix angle, tooth clearance
  (total backlash, applied half to each profile), centre bores for sun and
  planets, and assembled vs. in-a-row layout. Choose the input, output and
  fixed members and the feature reports the gear ratio, module, pitch and outer
  diameters and the planet-centre circle in its info message. Tooth profiles
  are computed in FeatureScript with the same trochoid math as the library.
- **Data-driven feature** from the "Onshape FeatureScript" card in the web app
  (or `fs_export.generate_featurescript` in Python): a Feature Studio with the
  current design's profiles embedded, for every gear type including cycloidal
  drives and the Nabtesco. Thickness, tooth type, helix angle, stage gap and
  layout stay editable in Onshape.

Both share one geometry builder (the section between the `builder begin/end`
markers), which draws each loop as ~30-point fit-spline segments so that
rotated sections loft into helical and herringbone teeth, cuts ring gears from
a wall cylinder, keeps bearing holes straight, and places instances with
patterns.

### Run locally

```bash
cd pygeartrain
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install --no-deps git+https://github.com/eelcohoogendoorn/pycomplex.git
cd ..
pygeartrain\.venv\Scripts\python web\build_bundle.py      # writes web/dist/bundle.zip
pygeartrain\.venv\Scripts\python -m http.server 8765 -d web
```

Then open <http://localhost:8765>. The bundle must be served over HTTP (not
`file://`) because it is fetched by a Web Worker.

## Desktop GUI

```bash
cd verros_tools\pygeartrain
.venv\Scripts\python pygeartrain_gui.py
```

Same catalogue as the web app, rendered with matplotlib. Also exports animated
GIFs and writes CAD curve files straight to a folder. "Export STEP solids"
writes the same per-part and assembly STEP files as the web app, using
[CadQuery](https://cadquery.readthedocs.io). CadQuery is optional; install it
without letting pip replace the pinned NumPy:

```bash
.venv\Scripts\python -m pip install --no-deps cadquery
.venv\Scripts\python -m pip install "cadquery-ocp>=7.9.3.1,<8" ezdxf "multimethod<2" runtype casadi nlopt==2.7.1 typing_extensions
```

## Library notes

`verros_tools/pygeartrain/` is the upstream library with these changes:

- `cad_export.py`, `specs.py`, `webapi.py` added.
- Plot and GIF helpers draw on an explicit figure/axes instead of matplotlib's
  global state, and work with matplotlib ≥ 3.10.
- `core/pga.py` implements the 2D projective-geometric-algebra motors in plain
  NumPy instead of numga (verified against numga in `test_pga`). This makes
  animation frames about 25× faster and removes numga from the browser bundle.
- `requirements.txt` replaces the conda environment. Two C-only transitive
  dependencies of pycomplex (fastcache, pycosat) are replaced by tiny shims; the
  library never calls into them.

Run the tests with `MPLBACKEND=Agg python -m pytest pygeartrain/test -q` from
inside `verros_tools/pygeartrain/`.
