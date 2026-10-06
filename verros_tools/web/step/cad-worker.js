// cad-worker.js -- Web Worker that builds STEP files from a SolidModel with
// replicad / OpenCascade.js.  Load with:
//
//     const w = new Worker('step/cad-worker.js', { type: 'module' });
//
// Messages in :
//     { id, cmd: 'build', model, options: { parts, assembly, curves } }
//     { id, cmd: 'ping' }
// Messages out:
//     { type: 'progress', text, fraction, stage? }          (while loading / building)
//     { id, ok: true, files: [{ name, data: Uint8Array }], timings, warnings }
//     { id, ok: true, pong: true, ready: boolean }           (reply to ping)
//     { id, ok: false, error: string }
//
// replicad and the OpenCascade WebAssembly build are fetched lazily from
// jsDelivr (pinned versions below) on the first 'build' request, once.  The
// wasm is ~23 MB uncompressed (~6.5 MB brotli over the wire).

import * as builder from './step_builder.js?v=__BUILD__';

const REPLICAD_VERSION = '1.1.0';
const OC_VERSION = '1.1.0';
const REPLICAD_URL = 'https://cdn.jsdelivr.net/npm/replicad@' + REPLICAD_VERSION + '/dist/replicad.js';
const OC_JS_URL = 'https://cdn.jsdelivr.net/npm/replicad-opencascadejs@' + OC_VERSION + '/dist/replicad_single.js';
const OC_WASM_URL = 'https://cdn.jsdelivr.net/npm/replicad-opencascadejs@' + OC_VERSION + '/dist/replicad_single.wasm';
const OC_WASM_APPROX_BYTES = 23 * 1024 * 1024;   // used for progress when Content-Length is missing/compressed

export { REPLICAD_URL, OC_JS_URL, OC_WASM_URL };

const isWorker = typeof self !== 'undefined' && typeof self.postMessage === 'function'
  && typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope;

function post(msg, transfer) {
  if (!isWorker) return;
  if (transfer && transfer.length) self.postMessage(msg, transfer);
  else self.postMessage(msg);
}

function progress(text, fraction, extra) {
  post(Object.assign({ type: 'progress', text, fraction: Math.max(0, Math.min(1, fraction)) }, extra || {}));
}

function errorText(e) {
  if (e == null) return 'unknown error';
  if (typeof e === 'number') return 'OpenCascade exception #' + e;   // emscripten throws pointers
  if (typeof e === 'string') return e;
  if (e.message) return e.message;
  try { return JSON.stringify(e); } catch (_) { return String(e); }
}

// ---------------------------------------------------------------------------
// lazy initialisation of replicad + OpenCascade
// ---------------------------------------------------------------------------

let initPromise = null;
let ready = false;

/** Download the wasm with progress reporting; returns an ArrayBuffer. */
async function fetchWasm(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error('failed to download OpenCascade wasm: HTTP ' + res.status);
  const total = Number(res.headers.get('Content-Length')) || 0;
  const encoded = !!res.headers.get('Content-Encoding');
  // with a compressed transfer Content-Length is the wire size, not the byte count we read
  const expected = (total && !encoded) ? total : OC_WASM_APPROX_BYTES;
  if (!res.body || !res.body.getReader) return res.arrayBuffer();
  const reader = res.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.byteLength;
    const mb = (received / 1048576).toFixed(1);
    progress('Downloading OpenCascade (' + mb + ' MB)', 0.1 + 0.6 * Math.min(1, received / expected), { stage: 'download' });
  }
  const out = new Uint8Array(received);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.byteLength; }
  return out.buffer;
}

async function initCad() {
  progress('Loading replicad ' + REPLICAD_VERSION, 0.02, { stage: 'load' });
  const [replicad, ocModule] = await Promise.all([import(REPLICAD_URL), import(OC_JS_URL)]);
  const ocInit = ocModule.default;
  if (typeof ocInit !== 'function') throw new Error('unexpected replicad-opencascadejs module shape');

  let wasmBinary = null;
  try {
    wasmBinary = await fetchWasm(OC_WASM_URL);
  } catch (e) {
    // let emscripten fetch it itself (no progress, but still works)
    progress('Downloading OpenCascade', 0.5, { stage: 'download' });
    wasmBinary = null;
  }

  progress('Initialising OpenCascade', 0.75, { stage: 'init' });
  const moduleArgs = {
    print: () => {},                      // STEP writer statistics are noise
    printErr: (s) => console.warn('[oc] ' + s),
    locateFile: (file) => (file.endsWith('.wasm') ? OC_WASM_URL : file),
  };
  if (wasmBinary) moduleArgs.wasmBinary = wasmBinary;
  const oc = await ocInit(moduleArgs);

  builder.configure({ replicad, oc });
  ready = true;
  progress('CAD kernel ready', 1, { stage: 'ready' });
  return { replicad, oc };
}

export function ensureCad() {
  if (!initPromise) {
    initPromise = initCad().catch((e) => {
      initPromise = null;   // allow a retry on the next request
      throw e;
    });
  }
  return initPromise;
}

// ---------------------------------------------------------------------------
// requests
// ---------------------------------------------------------------------------

async function handleBuild(msg) {
  await ensureCad();
  const options = Object.assign({ parts: true, assembly: true, curves: 'spline' }, msg.options || {});
  const result = await builder.buildStep(msg.model, options, (p) => progress(p.text, p.fraction, { stage: p.stage, part: p.part }));
  const files = result.parts.slice();
  if (result.assembly) files.push(result.assembly);
  return { files, timings: result.timings, warnings: result.warnings };
}

async function onMessage(ev) {
  const msg = ev && ev.data;
  const id = msg && msg.id;
  try {
    if (!msg || typeof msg !== 'object') throw new Error('malformed message');
    if (msg.cmd === 'ping') {
      post({ id, ok: true, pong: true, ready });
      return;
    }
    if (msg.cmd === 'build') {
      if (!msg.model) throw new Error("'build' needs a model");
      const { files, timings, warnings } = await handleBuild(msg);
      const transfer = files.map((f) => f.data.buffer);
      post({ id, ok: true, files, timings, warnings }, transfer);
      return;
    }
    throw new Error("unknown command '" + (msg && msg.cmd) + "'");
  } catch (e) {
    post({ id, ok: false, error: errorText(e) });
  }
}

if (isWorker) {
  self.addEventListener('message', onMessage);
  self.addEventListener('unhandledrejection', (ev) => {
    post({ type: 'progress', text: 'Internal error: ' + errorText(ev && ev.reason), fraction: 0, stage: 'error' });
  });
}
