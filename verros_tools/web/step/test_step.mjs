// test_step.mjs -- Node test for step/step_builder.js (and a syntax check of
// step/cad-worker.js).  Run it FROM a directory that has `replicad` and
// `replicad-opencascadejs` installed in node_modules, e.g.
//
//     cd <scratch>/stepdev && npm i replicad@1.1.0 replicad-opencascadejs@1.1.0
//     node <repo>/web/step/test_step.mjs [modelsDir] [outDir]
//
// modelsDir defaults to ../models (relative to cwd), outDir to ./step_out.
// Nothing is written into the repository.

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const cwd = process.cwd();
const modelsDir = path.resolve(cwd, process.argv[2] || '../models');
const outDir = path.resolve(cwd, process.argv[3] || 'step_out');
fs.mkdirSync(outDir, { recursive: true });

// resolve the CAD packages from the *current directory*, not from this file
const requireFromCwd = createRequire(path.join(cwd, '__resolve__.js'));
const replicad = await import(pathToFileURL(requireFromCwd.resolve('replicad')).href);
const ocInit = (await import(pathToFileURL(requireFromCwd.resolve('replicad-opencascadejs')).href)).default;
const builder = await import(pathToFileURL(path.join(here, 'step_builder.js')).href);

let failures = 0;
function check(cond, msg) {
  if (cond) console.log('  PASS ' + msg);
  else { failures++; console.log('  FAIL ' + msg); }
}
const fmt = (x, d = 2) => Number(x).toFixed(d);

// ---------------------------------------------------------------------------
// 0. the worker file must at least parse as an ES module
// ---------------------------------------------------------------------------
console.log('== worker syntax check');
{
  const workerSrc = path.join(here, 'cad-worker.js');
  const tmp = path.join(outDir, 'cad-worker.check.mjs');
  // make the relative import resolvable from the temp location
  fs.writeFileSync(tmp, fs.readFileSync(workerSrc, 'utf8').replace("'./step_builder.js'", JSON.stringify(pathToFileURL(path.join(here, 'step_builder.js')).href)));
  try {
    execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
    check(true, 'node --check cad-worker.js (as .mjs)');
  } catch (e) {
    check(false, 'node --check cad-worker.js: ' + (e.stderr || e.message));
  }
  // importing it in Node must not register a worker nor touch the network
  const mod = await import(pathToFileURL(tmp).href);
  check(typeof mod.ensureCad === 'function' && /^https:\/\/cdn\.jsdelivr\.net\/npm\/replicad@\d/.test(mod.REPLICAD_URL), 'cad-worker.js imports cleanly in Node, exports pinned CDN URLs');
}

// ---------------------------------------------------------------------------
// init
// ---------------------------------------------------------------------------
let t0 = Date.now();
const oc = await ocInit({ print: () => {}, printErr: () => {} });
builder.configure({ replicad, oc });
console.log('OpenCascade initialised in ' + (Date.now() - t0) + ' ms');

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
function isStep(data) {
  const head = new TextDecoder().decode(data.subarray(0, 32));
  const text = new TextDecoder().decode(data);
  return head.startsWith('ISO-10303-21') && (text.includes('MANIFOLD_SOLID_BREP') || text.includes('ADVANCED_BREP_SHAPE_REPRESENTATION'));
}

function meshVolume(shape, tol = 0.02) {
  const me = shape.mesh({ tolerance: tol, angularTolerance: 0.2 });
  const v = me.vertices, tr = me.triangles;
  let vol = 0;
  for (let i = 0; i < tr.length; i += 3) {
    const a = tr[i] * 3, b = tr[i + 1] * 3, c = tr[i + 2] * 3;
    vol += (v[a] * (v[b + 1] * v[c + 2] - v[b + 2] * v[c + 1])
      - v[a + 1] * (v[b] * v[c + 2] - v[b + 2] * v[c])
      + v[a + 2] * (v[b] * v[c + 1] - v[b + 1] * v[c])) / 6;
  }
  return Math.abs(vol);
}

function faceArea(face) {
  let a = builder.signedArea(builder.cleanLoop(face.outer));
  for (const h of face.holes || []) a -= Math.abs(builder.signedArea(builder.cleanLoop(h)));
  return Math.abs(a);
}

/** N-fold circular mean of angles (deg): the mean orientation of an N-periodic pattern. */
function periodicMean(anglesDeg, N) {
  let x = 0, y = 0;
  for (const a of anglesDeg) { const r = a * Math.PI / 180 * N; x += Math.cos(r); y += Math.sin(r); }
  return Math.atan2(y, x) * 180 / Math.PI / N;
}
function wrapPeriod(a, period) {
  return ((a % period) + 1.5 * period) % period - period / 2;
}

/**
 * Rotation (deg, CCW) of the outermost mesh vertices on the plane z = z0,
 * relative to the outermost profile points (which define the mid plane).
 */
function faceRotation(shape, part, z0, teeth) {
  const me = shape.mesh({ tolerance: 0.01, angularTolerance: 0.2 });
  const v = me.vertices;
  const sel = [];
  for (let i = 0; i < v.length; i += 3) {
    if (Math.abs(v[i + 2] - z0) < 1e-4) sel.push([Math.hypot(v[i], v[i + 1]), Math.atan2(v[i + 1], v[i]) * 180 / Math.PI]);
  }
  const pts = part.faces[0].outer;
  const rRef = Math.max(...pts.map((p) => Math.hypot(p[0], p[1])));
  const refOuter = pts.filter((p) => Math.hypot(p[0], p[1]) > rRef - 0.05).map((p) => Math.atan2(p[1], p[0]) * 180 / Math.PI);
  const rMax = Math.max(...sel.map((s) => s[0]));
  const outer = sel.filter((s) => s[0] > rMax - 0.05).map((s) => s[1]);
  const period = 360 / teeth;
  return { rot: wrapPeriod(periodicMean(outer, teeth) - periodicMean(refOuter, teeth), period), n: outer.length, nRef: refOuter.length };
}

function midPlaneRotation(shape, part, teeth) {
  // vertices close to z = 0 (there may be few of them on a swept surface)
  const me = shape.mesh({ tolerance: 0.01, angularTolerance: 0.2 });
  const v = me.vertices;
  const sel = [];
  for (let i = 0; i < v.length; i += 3) if (Math.abs(v[i + 2]) < 0.02) sel.push([Math.hypot(v[i], v[i + 1]), Math.atan2(v[i + 1], v[i]) * 180 / Math.PI]);
  if (!sel.length) return null;
  const pts = part.faces[0].outer;
  const rRef = Math.max(...pts.map((p) => Math.hypot(p[0], p[1])));
  const refOuter = pts.filter((p) => Math.hypot(p[0], p[1]) > rRef - 0.05).map((p) => Math.atan2(p[1], p[0]) * 180 / Math.PI);
  const rMax = Math.max(...sel.map((s) => s[0]));
  const outer = sel.filter((s) => s[0] > rMax - 0.05).map((s) => s[1]);
  if (!outer.length) return null;
  return { rot: wrapPeriod(periodicMean(outer, teeth) - periodicMean(refOuter, teeth), 360 / teeth), n: outer.length };
}

// ---------------------------------------------------------------------------
// 1 + 2. build every sample model, validate and write the STEP files
// ---------------------------------------------------------------------------
const modelFiles = fs.readdirSync(modelsDir).filter((f) => f.endsWith('.json')).sort();
if (!modelFiles.length) { console.log('no model json found in ' + modelsDir); process.exit(2); }
const models = {};
const grandTotal = { ms: 0 };

for (const file of modelFiles) {
  const name = path.basename(file, '.json');
  const model = JSON.parse(fs.readFileSync(path.join(modelsDir, file), 'utf8'));
  models[name] = model;
  console.log('\n== ' + name + ' (' + model.gear_type + ', ' + model.parts.length + ' parts, ' + model.instances.length + ' instances)');
  let lastProgress = '';
  const res = await builder.buildStep(model, { parts: true, assembly: true, curves: 'spline' }, (p) => { lastProgress = p.text; });
  for (const [pname, ms] of Object.entries(res.timings.parts)) {
    console.log('  part ' + pname.padEnd(20) + ' build ' + String(ms).padStart(6) + ' ms   export ' + String(res.timings.export[pname.replace(/[^\w.-]+/g, '_') + '.step'] || 0).padStart(6) + ' ms');
  }
  console.log('  assembly ' + res.timings.assembly_ms + ' ms, total ' + res.timings.total_ms + ' ms');
  grandTotal.ms += res.timings.total_ms;
  for (const w of res.warnings) console.log('  WARN ' + w);
  check(lastProgress === 'Done', 'progress callback reached Done');
  check(res.parts.length === model.parts.length && res.assembly, 'one STEP per part + assembly');

  const dir = path.join(outDir, name);
  fs.mkdirSync(dir, { recursive: true });
  const files = [...res.parts, res.assembly];
  let allStep = true;
  for (const f of files) {
    fs.writeFileSync(path.join(dir, f.name), f.data);
    if (!isStep(f.data)) { allStep = false; console.log('  not a STEP solid: ' + f.name); }
  }
  check(allStep, 'all ' + files.length + ' files start with ISO-10303-21 and contain MANIFOLD_SOLID_BREP (' + Math.round(files.reduce((s, f) => s + f.data.length, 0) / 1024) + ' kB)');

  // cross-section sanity: volume == profile area * thickness for every non-fused part
  for (const part of model.parts) {
    if (part.fuse) continue;
    const { shape } = builder.buildPartShape(part, model, { curves: 'spline' });
    const expected = part.faces.reduce((s, f) => s + faceArea(f), 0) * model.thickness_mm;
    const vol = meshVolume(shape);
    const rel = Math.abs(vol - expected) / expected;
    check(rel < 0.02, 'volume ' + part.name + ': ' + fmt(vol, 1) + ' vs area*t ' + fmt(expected, 1) + ' (' + fmt(rel * 100, 2) + ' %)');
  }
}
console.log('\nTotal build time over all models: ' + grandTotal.ms + ' ms');

// ---------------------------------------------------------------------------
// 3. twist direction on planetary sun_6 for helix and herringbone
// ---------------------------------------------------------------------------
console.log('\n== twist direction (planetary sun_6)');
{
  const planetary = models.planetary;
  const sun = planetary.parts.find((p) => p.name === 'sun_6');
  const t = planetary.thickness_mm;
  const h = sun.half_twist_deg;
  const teeth = 6;
  for (const gearType of ['helix', 'herringbone']) {
    const model = { ...planetary, gear_type: gearType };
    const { shape } = builder.buildPartShape(sun, model, { curves: 'spline' });
    const top = faceRotation(shape, sun, t / 2, teeth);
    const bot = faceRotation(shape, sun, -t / 2, teeth);
    const mid = midPlaneRotation(shape, sun, teeth);
    const expTop = h, expBot = gearType === 'helix' ? -h : h;
    console.log('  ' + gearType + ': half_twist ' + fmt(h, 3) + ' deg | top ' + fmt(top.rot, 3) + ' (n=' + top.n + ') | bottom ' + fmt(bot.rot, 3) + ' (n=' + bot.n + ')' + (mid ? ' | mid ' + fmt(mid.rot, 3) + ' (n=' + mid.n + ')' : ' | mid: no vertices at z=0'));
    check(Math.abs(top.rot - expTop) < 1, gearType + ' +z face rotated by ' + fmt(top.rot, 2) + ' deg, expected ' + fmt(expTop, 2));
    check(Math.abs(bot.rot - expBot) < 1, gearType + ' -z face rotated by ' + fmt(bot.rot, 2) + ' deg, expected ' + fmt(expBot, 2));
    if (mid) check(Math.abs(mid.rot) < 1, gearType + ' mid plane rotated by ' + fmt(mid.rot, 2) + ' deg, expected 0');
    const bb = shape.boundingBox.bounds;
    check(Math.abs(bb[0][2] + t / 2) < 1e-3 && Math.abs(bb[1][2] - t / 2) < 1e-3, gearType + ' z extent is [-t/2, +t/2]');
    const expectedVol = faceArea(sun.faces[0]) * t;
    const vol = meshVolume(shape);
    check(Math.abs(vol - expectedVol) / expectedVol < 0.02, gearType + ' volume ' + fmt(vol, 1) + ' vs ' + fmt(expectedVol, 1));
  }
  // a negative half twist (planet_12) must flip the sign
  const planet = planetary.parts.find((p) => p.name === 'planet_12');
  const { shape } = builder.buildPartShape(planet, { ...planetary, gear_type: 'helix' }, { curves: 'spline' });
  const top = faceRotation(shape, planet, t / 2, 12);
  console.log('  helix planet_12: half_twist ' + fmt(planet.half_twist_deg, 3) + ' | top ' + fmt(top.rot, 3));
  check(Math.abs(top.rot - planet.half_twist_deg) < 1, 'negative half twist handled');
}

// ---------------------------------------------------------------------------
// 4. assembly bounding boxes
// ---------------------------------------------------------------------------
console.log('\n== assembly bounding boxes');
{
  const compound = models.compound;
  const shapes = new Map(compound.parts.map((p) => [p.name, builder.buildPartShape(p, compound, { curves: 'spline' }).shape]));
  const asm = builder.buildAssemblyShape(compound, shapes);
  const bb = asm.boundingBox.bounds;
  const zExtent = bb[1][2] - bb[0][2];
  const expected = 2 * compound.thickness_mm + compound.layer_gap_mm;
  console.log('  compound assembly bbox z: [' + fmt(bb[0][2]) + ', ' + fmt(bb[1][2]) + '] extent ' + fmt(zExtent) + ', expected ' + fmt(expected));
  check(Math.abs(zExtent - expected) < 0.05, 'compound assembly z extent == 2*thickness + layer_gap');
  check(Math.abs(bb[0][2] + compound.thickness_mm / 2) < 0.05, 'compound assembly starts at -thickness/2');

  const planetary = models.planetary;
  const ring = planetary.parts.find((p) => p.name === 'ring_30');
  const ringShape = builder.buildPartShape(ring, planetary, { curves: 'spline' }).shape;
  const rb = ringShape.boundingBox.bounds;
  const rOuter = Math.max(-rb[0][0], rb[1][0], -rb[0][1], rb[1][1]);
  console.log('  ring_30 bbox radius ' + fmt(rOuter, 3) + ', outer_radius_mm ' + fmt(ring.outer_radius_mm, 3));
  check(Math.abs(rOuter - ring.outer_radius_mm) < 0.05, 'ring outer radius matches outer_radius_mm');
  const pshapes = new Map(planetary.parts.map((p) => [p.name, builder.buildPartShape(p, planetary, { curves: 'spline' }).shape]));
  const pasm = builder.buildAssemblyShape(planetary, pshapes);
  const pb = pasm.boundingBox.bounds;
  const pOuter = Math.max(-pb[0][0], pb[1][0], -pb[0][1], pb[1][1]);
  console.log('  planetary assembly bbox radius ' + fmt(pOuter, 3) + ', z [' + fmt(pb[0][2]) + ', ' + fmt(pb[1][2]) + ']');
  check(Math.abs(pOuter - ring.outer_radius_mm) < 0.05, 'planetary assembly radius == ring outer radius');
  check(Math.abs(pb[1][2] - pb[0][2] - planetary.thickness_mm) < 0.05, 'planetary assembly z extent == thickness (single layer)');
}

// ---------------------------------------------------------------------------
// 5. fuse: true parts and the polyline option
// ---------------------------------------------------------------------------
console.log('\n== fuse parts / polyline option');
{
  const circle = (cx, cy, r, n = 99) => Array.from({ length: n }, (_, i) => { const a = 2 * Math.PI * i / n; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; });
  const model = {
    thickness_mm: 4, layer_gap_mm: 1, gear_type: 'spur',
    parts: [
      { name: 'carrier_pins', fuse: true, half_twist_deg: 0, faces: [{ outer: circle(0, 0, 10), holes: [] }, { outer: circle(10, 0, 3), holes: [] }, { outer: circle(-10, 0, 3), holes: [] }] },
      { name: 'two_discs', half_twist_deg: 0, faces: [{ outer: circle(0, 0, 2), holes: [] }, { outer: circle(8, 0, 2), holes: [] }] },
    ],
    instances: [{ part: 'carrier_pins', angle_deg: 90, x_mm: 0, y_mm: 0, layer: 0 }, { part: 'two_discs', angle_deg: 0, x_mm: 0, y_mm: 0, layer: 1 }],
  };
  const fused = builder.buildPartShape(model.parts[0], model, {}).shape;
  check(fused.solids.length === 1, 'fuse:true part is a single solid (' + fused.solids.length + ')');
  // union area: big disc + two half-discs sticking out (each small circle is centred on the rim)
  const expectedArea = Math.PI * 100 + 2 * (Math.PI * 9 / 2);
  const vol = meshVolume(fused);
  check(Math.abs(vol - expectedArea * 4) / (expectedArea * 4) < 0.02, 'fused volume ' + fmt(vol, 1) + ' vs ' + fmt(expectedArea * 4, 1));
  const two = builder.buildPartShape(model.parts[1], model, {}).shape;
  check(two.solids.length === 2, 'non-fused multi-face part is a compound of 2 solids (' + two.solids.length + ')');
  const res = await builder.buildStep(model, { parts: true, assembly: true });
  check(res.parts.length === 2 && res.assembly && [...res.parts, res.assembly].every((f) => isStep(f.data)), 'fuse model exports valid STEP files');
  const bb = builder.buildAssemblyShape(model, new Map([['carrier_pins', fused], ['two_discs', two]])).boundingBox.bounds;
  check(Math.abs(bb[1][1] - 13) < 0.05 && Math.abs(bb[1][0] - 10) < 0.05, 'instance rotation by 90 deg puts the pins on the y axis');
  check(Math.abs(bb[1][2] - (5 + 2)) < 0.05, 'layer 1 lifts the mid plane to thickness + gap');

  // a hole exactly tangent to the outer loop (eccentric with zero wall) cannot
  // live in one planar face; the builder must fall back to a solid cut
  const eccModel = {
    thickness_mm: 10, layer_gap_mm: 1, gear_type: 'spur',
    parts: [{ name: 'eccentric_tangent', half_twist_deg: 0, faces: [{ outer: circle(1.7157, 0, 5.8333), holes: [circle(0, 0, 4.1176).reverse()] }] }],
    instances: [{ part: 'eccentric_tangent', angle_deg: 0, x_mm: 0, y_mm: 0, layer: 0 }],
  };
  const ecc = builder.buildPartShape(eccModel.parts[0], eccModel, {});
  const eccExpected = Math.PI * (5.8333 ** 2 - 4.1176 ** 2) * 10;
  const eccVol = meshVolume(ecc.shape);
  console.log('  tangent-hole eccentric: volume ' + fmt(eccVol, 1) + ' expected ' + fmt(eccExpected, 1) + (ecc.warnings.length ? ' | ' + ecc.warnings.join('; ') : ''));
  check(Math.abs(eccVol - eccExpected) / eccExpected < 0.02 && ecc.shape.solids.length === 1, 'tangent hole handled (solid-cut fallback)');

  const simple = models.simple;
  const t1 = Date.now();
  const poly = await builder.buildStep(simple, { parts: true, assembly: false, curves: 'polyline' });
  console.log('  simple.json with curves=polyline: ' + (Date.now() - t1) + ' ms, ' + Math.round(poly.parts.reduce((s, f) => s + f.data.length, 0) / 1024) + ' kB');
  check(poly.parts.every((f) => isStep(f.data)) && poly.assembly === null, 'polyline option works and assembly:false yields null');
  const onlyAsm = await builder.buildStep(simple, { parts: false, assembly: true });
  check(onlyAsm.parts.length === 0 && onlyAsm.assembly && isStep(onlyAsm.assembly.data), 'parts:false yields only the assembly');
}

console.log('\nSTEP files written to ' + outDir);
console.log(failures ? '\n' + failures + ' CHECK(S) FAILED' : '\nALL CHECKS PASSED');
process.exit(failures ? 1 : 0);
