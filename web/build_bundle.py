"""Build web/dist/bundle.zip: the pure-Python code the browser needs on top of
Pyodide's own numpy / scipy / sympy / shapely.

Contents:
  pygeartrain/              this repository's library (with cad_export, specs, webapi)
  pycomplex/, numpy_indexed/, funcsigs/, cached_property.py
                            pure-Python dependencies, installed with pip --target
  fastcache.py, pycosat.py  tiny shims for two C-only packages pycomplex imports
                            (never actually exercised by pygeartrain)

Usage:  python web/build_bundle.py            (needs network for pip the first time)
        python web/build_bundle.py --no-pip   (reuse an existing web/_deps directory)
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
DEPS = os.path.join(WEB, '_deps')
DIST = os.path.join(WEB, 'dist')
LIB = os.path.join(ROOT, 'pygeartrain', 'pygeartrain')

PIP_TARGETS = [
    'numpy-indexed==0.3.7',
    'cached-property',
    'funcsigs',
]
PIP_TARGETS_NO_DEPS = [
    'git+https://github.com/eelcohoogendoorn/pycomplex.git',
]
# only these top-level names from _deps go into the bundle
KEEP = ['pycomplex', 'numpy_indexed', 'funcsigs', 'cached_property.py']

SHIMS = {
    'fastcache.py': (
        '"""Shim for the C-only fastcache package."""\n'
        'from functools import lru_cache\n\n'
        'clru_cache = lru_cache\n'
    ),
    'pycosat.py': (
        '"""Shim for the C-only pycosat package. pygeartrain never reaches the SAT solver;\n'
        'this exists only so pycomplex can be imported."""\n\n\n'
        'def solve(clauses, *args, **kwargs):\n'
        '    raise NotImplementedError("pycosat is not available in the browser build")\n\n\n'
        'def itersolve(clauses, *args, **kwargs):\n'
        '    raise NotImplementedError("pycosat is not available in the browser build")\n'
    ),
}

SKIP_DIRS = {'__pycache__', 'test', 'tests', 'notebooks', 'docs', 'examples'}
SKIP_EXT = {'.pyc', '.pyo', '.ipynb', '.png', '.gif', '.jpg', '.md', '.txt', '.toml', '.lock', '.cfg'}


def pip_install():
    if os.path.isdir(DEPS):
        shutil.rmtree(DEPS)
    os.makedirs(DEPS)
    base = [sys.executable, '-m', 'pip', 'install', '--quiet', '--target', DEPS, '--no-compile']
    # numpy is provided by Pyodide; keep pip from dragging a wheel of it into _deps
    subprocess.check_call(base + ['--no-deps'] + PIP_TARGETS + PIP_TARGETS_NO_DEPS)


def add_tree(z: zipfile.ZipFile, src_dir: str, arc_root: str):
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith('.dist-info')]
        for fn in filenames:
            if os.path.splitext(fn)[1] in SKIP_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src_dir)
            z.write(full, os.path.join(arc_root, rel))


def build():
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, 'bundle.zip')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        add_tree(z, LIB, 'pygeartrain')
        for name in KEEP:
            src = os.path.join(DEPS, name)
            if os.path.isdir(src):
                add_tree(z, src, name)
            elif os.path.isfile(src):
                z.write(src, name)
            else:
                raise SystemExit(f'missing dependency in {DEPS}: {name}')
        for name, text in SHIMS.items():
            z.writestr(name, text)
    size = os.path.getsize(out) / 1024
    print(f'wrote {out} ({size:.0f} KB)')


if __name__ == '__main__':
    if '--no-pip' not in sys.argv:
        pip_install()
    build()
