// Web Worker: runs pygeartrain inside Pyodide so the UI thread stays responsive.
// Messages in:  {id, cmd: 'build'|'frame'|'scene'|'export'|'export_report', ...}
// Messages out: {type:'status'|'ready'|'error'} or {id, ok, ...}

const PYODIDE_VERSION = '0.26.4';
const INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

importScripts(`${INDEX_URL}pyodide.js`);

let pyodide = null;
let api = null;

function status(text, progress) {
  postMessage({ type: 'status', text, progress });
}

async function init() {
  try {
    status('Starting Python runtime…', 0.05);
    pyodide = await loadPyodide({ indexURL: INDEX_URL });

    status('Loading numpy, scipy, sympy, shapely…', 0.15);
    let loaded = 0;
    await pyodide.loadPackage(['numpy', 'scipy', 'sympy', 'shapely'], {
      messageCallback: (m) => {
        if (/^Loaded /.test(m)) {
          loaded += 1;
          status(m, Math.min(0.15 + loaded * 0.08, 0.8));
        }
      },
      errorCallback: (m) => console.warn(m),
    });

    status('Fetching gear library…', 0.85);
    const resp = await fetch(new URL('dist/bundle.zip', self.location.href), { cache: 'no-cache' });
    if (!resp.ok) throw new Error(`bundle.zip: HTTP ${resp.status}`);
    const buf = await resp.arrayBuffer();
    pyodide.unpackArchive(buf, 'zip', { extractDir: '/home/pyodide/pkgs' });
    pyodide.runPython("import sys; sys.path.insert(0, '/home/pyodide/pkgs')");

    status('Importing pygeartrain…', 0.92);
    api = pyodide.pyimport('pygeartrain.webapi');
    const specs = JSON.parse(api.list_specs());
    status('Ready', 1);
    postMessage({ type: 'ready', specs });
  } catch (e) {
    console.error(e);
    postMessage({ type: 'error', text: String(e && e.message ? e.message : e) });
  }
}

function handle(msg) {
  const { id, cmd } = msg;
  try {
    if (!api) throw new Error('Python runtime not ready yet');
    if (cmd === 'build') {
      const result = JSON.parse(api.build(msg.spec, JSON.stringify(msg.kin), JSON.stringify(msg.params)));
      postMessage({ id, ok: true, result });
    } else if (cmd === 'frame') {
      const meta = JSON.parse(api.frame(msg.phase));
      const arr = api.frame_buffer();
      const view = arr.getBuffer('f32');
      const data = view.data.slice(); // copy out of the WASM heap
      view.release();
      arr.destroy();
      postMessage({ id, ok: true, meta, data, phase: msg.phase }, [data.buffer]);
    } else if (cmd === 'scene') {
      postMessage({ id, ok: true, scene: JSON.parse(api.angular_scene()) });
    } else if (cmd === 'export') {
      const zip = api.export_zip(JSON.stringify(msg.settings));
      const bytes = zip.toJs();
      zip.destroy();
      postMessage({ id, ok: true, bytes }, [bytes.buffer]);
    } else if (cmd === 'dimensions') {
      postMessage({ id, ok: true, dims: JSON.parse(api.dimensions(JSON.stringify(msg.settings))) });
    } else if (cmd === 'solid_model') {
      postMessage({ id, ok: true, model: JSON.parse(api.solid_model(JSON.stringify(msg.settings))) });
    } else if (cmd === 'export_report') {
      postMessage({ id, ok: true, report: JSON.parse(api.export_report(JSON.stringify(msg.settings))) });
    } else {
      throw new Error(`unknown command ${cmd}`);
    }
  } catch (e) {
    console.error(e);
    postMessage({ id, ok: false, error: String(e && e.message ? e.message : e) });
  }
}

onmessage = (ev) => handle(ev.data);
init();
