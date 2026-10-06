// step_builder.js -- turn a SolidModel JSON description of a gear train into
// STEP files with replicad (https://replicad.xyz) on top of OpenCascade.js.
//
// This module is pure: it has no imports of its own.  The replicad namespace
// (and, optionally, the initialised OpenCascade instance) is injected with
// `configure({replicad, oc})` or through the `deps` argument of `buildStep`,
// so the very same file runs in the browser worker (step/cad-worker.js, where
// replicad comes from a CDN) and in Node (step/test_step.mjs, where it comes
// from node_modules).
//
// SolidModel schema: see pygeartrain/solid_model.py.  In short:
//   { thickness_mm, layer_gap_mm, gear_type: 'spur'|'helix'|'herringbone',
//     parts: [{ name, fuse?, half_twist_deg, faces: [{outer: [[x,y]..], holes: [[[x,y]..]..]}] }],
//     instances: [{ part, angle_deg, x_mm, y_mm, layer }] }
//
// Geometry conventions (all angles CCW positive when viewed from +z):
//   * every part is built centred on its own origin with its mid plane at z = 0;
//   * half_twist_deg is the rotation of the z = +t/2 face relative to z = 0;
//     helix       : z = -t/2 face rotated by -half_twist  (one continuous helix)
//     herringbone : z = -t/2 face rotated by +half_twist  (V shape, apex at z = 0)
//     spur        : no twist;
//   * an instance is rotated by angle_deg about z, translated by (x_mm, y_mm)
//     and lifted so its mid plane sits at z = layer * (thickness + gap).

let R = null;            // replicad namespace
let configured = false;

/** Inject replicad (and optionally the OpenCascade instance it should use). */
export function configure({ replicad, oc } = {}) {
  if (!replicad) throw new Error('configure(): replicad namespace is required');
  R = replicad;
  if (oc) R.setOC(oc);
  configured = true;
}

export function isConfigured() {
  return configured;
}

function need() {
  if (!R) throw new Error('step_builder is not configured: call configure({replicad, oc}) first');
  return R;
}

// ---------------------------------------------------------------------------
// 2D profiles
// ---------------------------------------------------------------------------

/** Above this many points a loop is approximated by several spline pieces. */
const MAX_SINGLE_SPLINE_POINTS = 3000;
const SPLINE_CHUNK = 1500;
const SPLINE_TOLERANCE = 1e-3;   // mm, approximation tolerance of the B-spline fit

/** Drop consecutive duplicates and a repeated closing point. */
export function cleanLoop(points) {
  const out = [];
  for (const p of points) {
    const q = out[out.length - 1];
    if (!q || Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-9) out.push([Number(p[0]), Number(p[1])]);
  }
  if (out.length > 1) {
    const a = out[0], b = out[out.length - 1];
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) <= 1e-9) out.pop();
  }
  if (out.length < 3) throw new Error('loop needs at least 3 distinct points, got ' + out.length);
  return out;
}

/** Signed polygon area (positive = counter-clockwise). */
export function signedArea(points) {
  let a = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

/** Blueprint of a closed smooth curve through the points (B-spline fit). */
function splineBlueprint(points) {
  const R = need();
  if (points.length <= MAX_SINGLE_SPLINE_POINTS) {
    // repeat the first point so the fitted curve closes on itself; with the
    // end points identical drawPointsInterpolation adds no closing segment.
    return R.drawPointsInterpolation([...points, points[0]], { tolerance: SPLINE_TOLERANCE }, { closeShape: true }).blueprint;
  }
  // very long loops: several spline pieces joined end to end (C0 at the seams,
  // but the pieces pass through shared points so the tangent jump is tiny)
  const curves = [];
  for (let i = 0; i < points.length; i += SPLINE_CHUNK) {
    const chunk = points.slice(i, Math.min(i + SPLINE_CHUNK + 1, points.length));
    if (i + SPLINE_CHUNK + 1 > points.length) chunk.push(points[0]);
    curves.push(...R.drawPointsInterpolation(chunk, { tolerance: SPLINE_TOLERANCE }).blueprint.curves);
  }
  return new R.Blueprint(curves);
}

/** Blueprint of the closed polyline through the points. */
function polylineBlueprint(points) {
  const R = need();
  const pen = R.draw(points[0]);
  for (let i = 1; i < points.length; i++) pen.lineTo(points[i]);
  return pen.close().blueprint;
}

/**
 * Build the replicad Drawing of one face ({outer, holes}).
 * `curves` is 'spline' (default) or 'polyline'.  When a spline fit fails the
 * loop silently falls back to a polyline and the loop is listed in `warnings`.
 */
export function makeFaceDrawing(face, curves = 'spline', warnings = null) {
  const R = need();
  const loopBp = (pts, what) => {
    const clean = cleanLoop(pts);
    if (curves === 'polyline') return polylineBlueprint(clean);
    try {
      return splineBlueprint(clean);
    } catch (e) {
      if (warnings) warnings.push(what + ': spline fit failed (' + (e && e.message) + '); used polyline');
      return polylineBlueprint(clean);
    }
  };
  const outer = loopBp(face.outer, 'outer loop');
  const holes = (face.holes || []).map((h, i) => loopBp(h, 'hole ' + i));
  const shape2d = holes.length ? new R.CompoundBlueprint([outer, ...holes]) : outer;
  return new R.Drawing(shape2d);
}

// ---------------------------------------------------------------------------
// 3D solids
// ---------------------------------------------------------------------------

/** Replace a compound holding a single solid by that solid. */
function unwrapSingleSolid(shape) {
  try {
    const solids = shape.solids;
    if (solids && solids.length === 1 && shape.constructor && shape.constructor.name === 'Compound') return solids[0];
  } catch (_) { /* keep the shape as is */ }
  return shape;
}

/**
 * Herringbone solid without a boolean: sweep the upper half (0..halfT, twist h)
 * as open shells, mirror them through z = 0, close with the top face and its
 * mirror image and sew everything into one solid.  30-50x faster than fusing
 * two half solids and gives the same geometry.
 */
function herringboneFromShells(sketch, halfT, h) {
  const R = need();
  if (sketch instanceof R.Sketches) {
    // disjoint regions (a fused drawing that stays in several pieces)
    return R.makeCompound(sketch.sketches.map((s) => herringboneFromShells(s, halfT, h)));
  }
  const wires = sketch instanceof R.CompoundSketch ? sketch.sketches.map((s) => s.wire) : [sketch.wire];
  const shells = [];
  const endWires = [];
  for (const wire of wires) {
    const [shell, , endWire] = R.twistExtrude(wire, h, [0, 0, 0], [0, 0, halfT], undefined, true);
    shells.push(shell);
    endWires.push(endWire);
  }
  let top = R.makeFace(endWires[0]);
  if (endWires.length > 1) top = R.addHolesInFace(top, endWires.slice(1));
  const bottom = top.clone().mirror('XY');
  const lower = shells.map((s) => s.clone().mirror('XY'));
  return R.makeSolid([top, ...shells, ...lower, bottom]);
}

/**
 * Extrude one face drawing into a solid of the given thickness, mid plane at
 * z = 0, twisted about the z axis through the origin as the gear type dictates.
 * `zScale` > 1 lengthens the solid symmetrically (the twist per mm is kept);
 * it is used for cutting tools.  `warnings` collects fallbacks that were taken.
 */
export function extrudeFace(drawing, thickness, halfTwistDeg, gearType, zScale = 1, warnings = null) {
  const t = thickness * zScale;
  const h = (Number(halfTwistDeg) || 0) * zScale;
  const type = String(gearType || 'spur').toLowerCase();
  if (type === 'spur' || Math.abs(h) < 1e-9) {
    return drawing.sketchOnPlane('XY', -t / 2).extrude(t);
  }
  if (type === 'helix') {
    // one continuous twist of 2h over the full thickness, then re-centre so the
    // untwisted profile sits on the mid plane: top face at +h, bottom at -h.
    return drawing.sketchOnPlane('XY', 0).extrude(t, { twistAngle: 2 * h })
      .rotate(-h, [0, 0, 0], [0, 0, 1])
      .translateZ(-t / 2);
  }
  if (type === 'herringbone') {
    // upper half twisted by +h, mirrored through z = 0 (which flips the hand so
    // the lower face also ends up at +h), joined into one solid.
    try {
      return herringboneFromShells(drawing.sketchOnPlane('XY', 0), t / 2, h);
    } catch (e) {
      if (warnings) warnings.push('herringbone shell sewing failed (' + (e && e.message ? e.message : e) + '); used boolean fuse');
    }
    const upper = drawing.sketchOnPlane('XY', 0).extrude(t / 2, { twistAngle: h });
    const lower = upper.clone().mirror('XY');
    return unwrapSingleSolid(upper.fuse(lower));
  }
  throw new Error("unknown gear_type '" + gearType + "'");
}

/** Area of a face polygon (outer minus holes), from the raw point loops. */
export function faceArea(face) {
  let a = Math.abs(signedArea(cleanLoop(face.outer)));
  for (const h of face.holes || []) a -= Math.abs(signedArea(cleanLoop(h)));
  return a;
}

/** Relative tolerance for the planar-face area check of faces with holes. */
const HOLE_AREA_TOLERANCE = 0.03;

/**
 * Area of a planar face from its triangulation.  (GProp integration, which
 * replicad's measureArea uses, is unreliable on faces bounded by long B-splines.)
 */
function meshedArea(face) {
  const me = face.mesh({ tolerance: 0.01, angularTolerance: 0.3 });
  const v = me.vertices, tr = me.triangles;
  let area = 0;
  for (let i = 0; i < tr.length; i += 3) {
    const a = tr[i] * 3, b = tr[i + 1] * 3, c = tr[i + 2] * 3;
    const ux = v[b] - v[a], uy = v[b + 1] - v[a + 1], uz = v[b + 2] - v[a + 2];
    const wx = v[c] - v[a], wy = v[c + 1] - v[a + 1], wz = v[c + 2] - v[a + 2];
    area += Math.hypot(uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx) / 2;
  }
  return area;
}

/**
 * Solid of one face.  Faces whose holes do not come out right as a planar face
 * (e.g. a hole tangent to the outer loop, as on an eccentric) are rebuilt by
 * cutting the extruded hole solids out of the extruded outer solid.
 */
function faceSolid(face, t, h, gearType, curves, warnings) {
  const R = need();
  const drawing = makeFaceDrawing(face, curves, warnings);
  const holes = face.holes || [];
  if (holes.length) {
    let ok = true;
    try {
      const planar = drawing.sketchOnPlane('XY').face();
      const expected = faceArea(face);
      ok = Math.abs(meshedArea(planar) - expected) <= HOLE_AREA_TOLERANCE * Math.abs(expected);
    } catch (_) {
      ok = false;
    }
    if (!ok) {
      warnings.push('holes could not be embedded in the planar face; cut them as solids instead');
      let solid = extrudeFace(makeFaceDrawing({ outer: face.outer, holes: [] }, curves, warnings), t, h, gearType, 1, warnings);
      for (const hole of holes) {
        const tool = extrudeFace(makeFaceDrawing({ outer: hole, holes: [] }, curves, warnings), t, h, gearType, 1.02, warnings);
        solid = solid.cut(tool);
      }
      return unwrapSingleSolid(solid);
    }
  }
  return extrudeFace(drawing, t, h, gearType, 1, warnings);
}

/**
 * Build the solid (or compound of solids) of one part, centred on its origin.
 * Returns {shape, warnings}.
 */
export function buildPartShape(part, model, options = {}) {
  const R = need();
  const curves = options.curves === 'polyline' ? 'polyline' : 'spline';
  const warnings = [];
  const t = Number(model.thickness_mm);
  if (!(t > 0)) throw new Error('thickness_mm must be positive');
  const faces = part.faces || [];
  if (!faces.length) throw new Error("part '" + part.name + "' has no faces");

  const h = part.half_twist_deg;
  const type = model.gear_type;
  const wrapErr = (i, e) => new Error("part '" + part.name + "' face " + i + ': ' + (e && e.message ? e.message : e));

  if (part.fuse && faces.length > 1) {
    // overlapping / touching regions -> one solid.  Union them in 2D first
    // (cheap); if the 2D boolean fails, fuse the extruded solids instead.
    let fused = null;
    try {
      fused = makeFaceDrawing(faces[0], curves, warnings);
      for (let i = 1; i < faces.length; i++) fused = fused.fuse(makeFaceDrawing(faces[i], curves, warnings));
    } catch (e) {
      warnings.push('2D union failed (' + (e && e.message ? e.message : e) + '); fused the extruded solids');
      fused = null;
    }
    if (fused) {
      try {
        return { shape: unwrapSingleSolid(extrudeFace(fused, t, h, type, 1, warnings)), warnings };
      } catch (e) {
        throw wrapErr('(fused)', e);
      }
    }
    let shape = null;
    faces.forEach((face, i) => {
      let solid;
      try { solid = faceSolid(face, t, h, type, curves, warnings); } catch (e) { throw wrapErr(i, e); }
      shape = shape ? shape.fuse(solid) : solid;
    });
    return { shape: unwrapSingleSolid(shape), warnings };
  }

  const solids = faces.map((face, i) => {
    try {
      return faceSolid(face, t, h, type, curves, warnings);
    } catch (e) {
      throw wrapErr(i, e);
    }
  });
  const shape = solids.length === 1 ? solids[0] : R.makeCompound(solids);
  return { shape, warnings };
}

/** z of the mid plane of an instance. */
export function instanceZ(model, inst) {
  return (Number(inst.layer) || 0) * (Number(model.thickness_mm) + (Number(model.layer_gap_mm) || 0));
}

/** Copy of a part shape placed as the instance says. */
export function placeInstance(shape, inst, model) {
  const angle = Number(inst.angle_deg) || 0;
  let s = shape.clone();
  if (Math.abs(angle) > 1e-12) s = s.rotate(angle, [0, 0, 0], [0, 0, 1]);
  return s.translate(Number(inst.x_mm) || 0, Number(inst.y_mm) || 0, instanceZ(model, inst));
}

/** Compound of all placed instances (shapesByName: Map name -> shape). */
export function buildAssemblyShape(model, shapesByName) {
  const R = need();
  const placed = [];
  for (const inst of model.instances || []) {
    const shape = shapesByName.get(inst.part);
    if (!shape) throw new Error("instance refers to unknown part '" + inst.part + "'");
    placed.push(placeInstance(shape, inst, model));
  }
  if (!placed.length) throw new Error('model has no instances');
  return R.makeCompound(placed);
}

// ---------------------------------------------------------------------------
// export
// ---------------------------------------------------------------------------

export async function shapeToStep(shape) {
  const blob = shape.blobSTEP();
  return new Uint8Array(await blob.arrayBuffer());
}

function safeFileName(name) {
  return String(name).replace(/[^\w.-]+/g, '_');
}

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

/**
 * Build STEP files for a SolidModel.
 *
 * options  : { parts: true, assembly: true, curves: 'spline' | 'polyline' }
 * onProgress: ({text, fraction, stage, part}) => void   (optional)
 * deps     : { replicad, oc }  (optional, same as calling configure first)
 *
 * returns  : { parts: [{name, data: Uint8Array}], assembly: {name, data} | null,
 *              timings: { total_ms, parts: {name: ms}, export: {name: ms}, assembly_ms },
 *              warnings: string[] }
 */
export async function buildStep(model, options = {}, onProgress = null, deps = null) {
  if (deps) configure(deps);
  need();
  const opts = {
    parts: options.parts !== false,
    assembly: options.assembly !== false,
    curves: options.curves === 'polyline' ? 'polyline' : 'spline',
  };
  if (!model || !Array.isArray(model.parts)) throw new Error('invalid SolidModel: parts[] missing');
  const progress = (text, fraction, extra = {}) => {
    if (typeof onProgress === 'function') {
      try { onProgress({ text, fraction: Math.max(0, Math.min(1, fraction)), ...extra }); } catch (_) { /* ignore */ }
    }
  };

  const tStart = now();
  const timings = { total_ms: 0, parts: {}, export: {}, assembly_ms: 0 };
  const warnings = [];
  const nParts = model.parts.length;
  const steps = nParts + (opts.assembly ? 1 : 0);
  const shapes = new Map();
  const partFiles = [];

  for (let i = 0; i < nParts; i++) {
    const part = model.parts[i];
    progress('Building ' + part.name, i / steps, { stage: 'part', part: part.name });
    const t0 = now();
    const built = buildPartShape(part, model, opts);
    timings.parts[part.name] = Math.round(now() - t0);
    for (const msg of built.warnings) warnings.push(part.name + ': ' + msg);
    shapes.set(part.name, built.shape);
    if (opts.parts) {
      const name = safeFileName(part.name) + '.step';
      progress('Writing ' + name, (i + 0.7) / steps, { stage: 'export', part: part.name });
      const t1 = now();
      partFiles.push({ name, data: await shapeToStep(built.shape) });
      timings.export[name] = Math.round(now() - t1);
    }
  }

  let assembly = null;
  if (opts.assembly) {
    progress('Building assembly', nParts / steps, { stage: 'assembly' });
    const t0 = now();
    const compound = buildAssemblyShape(model, shapes);
    const data = await shapeToStep(compound);
    timings.assembly_ms = Math.round(now() - t0);
    assembly = { name: 'assembly.step', data };
  }

  timings.total_ms = Math.round(now() - tStart);
  progress('Done', 1, { stage: 'done' });
  return { parts: partFiles, assembly, timings, warnings };
}
