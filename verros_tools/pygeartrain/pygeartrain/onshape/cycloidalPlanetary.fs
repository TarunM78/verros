FeatureScript 1948;
import(path : "onshape/std/geometry.fs", version : "1948.0");

/*
 * Cycloidal planetary gear train (verros / pygeartrain)
 *
 * Builds a single or compound (Wolfrom) planetary with the epi/hypo-cycloidal
 * tooth profile of https://github.com/CKraft11/pygeartrain, as spur, helical or
 * herringbone gears, either assembled in mesh or laid out in a row for printing.
 *
 * Paste this file into a Feature Studio, then add the "Cycloidal planetary"
 * feature in a Part Studio.  Assembly condition: R = S + 2P and (R + S) divisible
 * by the number of planets for equal spacing.
 */

export enum SizeBy
{
    annotation { "Name" : "Ring outer diameter" }
    RING_OUTER,
    annotation { "Name" : "Planet centre circle diameter" }
    PLANET_CIRCLE
}

export const TEETH_BOUNDS = { (unitless) : [1, 12, 400] } as IntegerBoundSpec;
export const PLANET_COUNT_BOUNDS = { (unitless) : [1, 3, 40] } as IntegerBoundSpec;
export const MIX_BOUNDS = { (unitless) : [0.05, 0.5, 0.95] } as RealBoundSpec;
export const SIZE_BOUNDS = { (millimeter) : [1, 70, 5000] } as LengthBoundSpec;
export const MARGIN_BOUNDS = { (unitless) : [0.01, 0.12, 2] } as RealBoundSpec;
export const RES_BOUNDS = { (unitless) : [100, 500, 2000] } as IntegerBoundSpec;

annotation { "Feature Type Name" : "Cycloidal planetary",
             "Feature Type Description" : "Single or compound planetary gear train with cycloidal teeth (pygeartrain)" }
export const cycloidalPlanetary = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Ring teeth (R)" }
        isInteger(definition.ringTeeth, TEETH_BOUNDS);
        annotation { "Name" : "Planet teeth (P)" }
        isInteger(definition.planetTeeth, TEETH_BOUNDS);
        annotation { "Name" : "Sun teeth (S)" }
        isInteger(definition.sunTeeth, TEETH_BOUNDS);
        annotation { "Name" : "Number of planets (N)" }
        isInteger(definition.planetCount, PLANET_COUNT_BOUNDS);
        annotation { "Name" : "Epi/hypo mix b" }
        isReal(definition.mix, MIX_BOUNDS);

        annotation { "Name" : "Compound (second stage)" }
        definition.compound is boolean;
        if (definition.compound)
        {
            annotation { "Name" : "Stage 2 ring teeth (R2)" }
            isInteger(definition.ringTeeth2, TEETH_BOUNDS);
            annotation { "Name" : "Stage 2 planet teeth (P2)" }
            isInteger(definition.planetTeeth2, TEETH_BOUNDS);
            annotation { "Name" : "Stage 2 sun teeth (S2)" }
            isInteger(definition.sunTeeth2, TEETH_BOUNDS);
            annotation { "Name" : "Stage 2 epi/hypo mix b2" }
            isReal(definition.mix2, MIX_BOUNDS);
            annotation { "Name" : "Gap between stages" }
            isLength(definition.stageGap, GAP_BOUNDS);
        }

        annotation { "Name" : "Size by" }
        definition.sizeBy is SizeBy;
        annotation { "Name" : "Size" }
        isLength(definition.size, SIZE_BOUNDS);
        annotation { "Name" : "Face width (thickness)" }
        isLength(definition.thickness, THICKNESS_BOUNDS);
        annotation { "Name" : "Tooth type" }
        definition.toothType is ToothType;
        if (definition.toothType != ToothType.SPUR)
        {
            annotation { "Name" : "Helix angle" }
            isAngle(definition.helixAngle, HELIX_BOUNDS);
        }
        annotation { "Name" : "Ring wall (fraction of radius)" }
        isReal(definition.ringMargin, MARGIN_BOUNDS);
        annotation { "Name" : "Layout" }
        definition.layout is Layout;
        annotation { "Name" : "Profile resolution" }
        isInteger(definition.resolution, RES_BOUNDS);
    }
    {
        const stages = definition.compound ? 2 : 1;
        var model = { "parts" : [], "instances" : [] };
        var scale = 1.0;
        // stage 1 defines the scale: both stages share the carrier (planet centres at unit radius)
        for (var s = 0; s < stages; s += 1)
        {
            const R = s == 0 ? definition.ringTeeth : definition.ringTeeth2;
            const P = s == 0 ? definition.planetTeeth : definition.planetTeeth2;
            const S = s == 0 ? definition.sunTeeth : definition.sunTeeth2;
            const b = s == 0 ? definition.mix : definition.mix2;
            const N = definition.planetCount;
            // second stage profiles are offset by half a planet tooth, as in pygeartrain
            const offset = s == 0 ? 0 : 0.5 * P;
            const stage = planetaryProfiles(R, P, S, N, b, offset, definition.resolution);
            if (s == 0)
            {
                if (definition.sizeBy == SizeBy.RING_OUTER)
                {
                    scale = (definition.size / 2) / (maxRadius(stage.ring) * millimeter);
                }
                else
                {
                    scale = (definition.size / 2) / (1.0 * millimeter);
                }
            }
            const prefix = stages == 1 ? "" : ("stage" ~ (s + 1) ~ "_");
            const names = ["ring", "planet", "sun"];
            const hands = [-1, -1, 1];
            const loops = [stage.ring, stage.planet, stage.sun];
            const counts = [R, P, S];
            for (var k = 0; k < 3; k += 1)
            {
                const pts = scalePoints(loops[k], scale);
                const rMax = maxRadius(pts);
                var faces;
                if (k == 0)
                {
                    // ring gear: outer wall circle with the tooth loop as a hole
                    const outer = circlePoints(rMax * (1 + definition.ringMargin), 180);
                    faces = [{ "outer" : outer, "holes" : [pts] }];
                }
                else
                {
                    faces = [{ "outer" : pts, "holes" : [] }];
                }
                model.parts = append(model.parts, {
                    "name" : prefix ~ names[k] ~ "_" ~ counts[k],
                    "faces" : faces,
                    "internal" : k == 0,
                    "fuse" : false,
                    "refRadius" : rMax,
                    "hand" : hands[k]
                });
            }
            // placements (phase 0): ring and sun on axis, planets around the carrier
            model.instances = append(model.instances, { "part" : prefix ~ "ring_" ~ R, "angle" : 0, "x" : 0, "y" : 0, "layer" : s });
            model.instances = append(model.instances, { "part" : prefix ~ "sun_" ~ S, "angle" : 0, "x" : 0, "y" : 0, "layer" : s });
            for (var i = 0; i < N; i += 1)
            {
                const a = 2 * PI * i / N;
                const w = (1 - R / P) * a; // rotation that keeps the planet meshing along the carrier
                model.instances = append(model.instances, {
                    "part" : prefix ~ "planet_" ~ P, "angle" : w,
                    "x" : scale * cos(a * radian), "y" : scale * sin(a * radian), "layer" : s
                });
            }
        }
        buildGearTrain(context, id, model, {
            "thickness" : definition.thickness,
            "layerGap" : definition.compound ? definition.stageGap : 0 * millimeter,
            "toothType" : definition.toothType,
            "helixAngle" : definition.toothType == ToothType.SPUR ? 0 * degree : definition.helixAngle,
            "layout" : definition.layout,
            "smooth" : true
        });
    });

// ---------------------------------------------------------------------------
// Cycloidal (epi/hypo trochoid) tooth profiles, a port of pygeartrain.core.profiles
// Points are plain [x, y] number pairs in "library units": planet centres at radius 1.
// ---------------------------------------------------------------------------

function trochoidPart(R is number, r is number, s is number, res is number) returns array
{
    // one tooth flank: a point on a circle of radius r rolling outside (s=+1) or inside (s=-1) a circle of radius R
    const N = R / r;
    const n = floor(r * res);
    var pts = [];
    for (var i = 0; i < n; i += 1)
    {
        const a = (2 * PI / N) * i / n;
        const b = (R + r * s) / r * a * s;
        pts = append(pts, [(R + r * s) * cos(a * radian) - r * s * cos(b * radian),
                           (R + r * s) * sin(a * radian) - r * s * sin(b * radian)]);
    }
    return pts;
}

function epiHypoGear(R is number, N is number, f is number, res is number) returns array
{
    // alternating epi and hypo trochoid sections; f is the epi fraction of a tooth pitch
    const r = R / N;
    const t = 2 * PI / N;
    const p = trochoidPart(R, r * f, 1, res);
    const n = trochoidPart(R, r * (1 - f), -1, res);
    var u = [];
    for (var i = 0; i < size(p); i += 1)
    {
        u = append(u, rotate2(p[i], -t * f / 2));
    }
    for (var i = 0; i < size(n); i += 1)
    {
        u = append(u, rotate2(n[i], t * f / 2));
    }
    var c = [];
    for (var k = 0; k < N; k += 1)
    {
        for (var i = 0; i < size(u); i += 1)
        {
            c = append(c, rotate2(u[i], t * k));
        }
    }
    return c;
}

function rotatePoints(pts is array, angle is number) returns array
{
    var out = [];
    for (var i = 0; i < size(pts); i += 1)
    {
        out = append(out, rotate2(pts[i], angle));
    }
    return out;
}

function planetaryProfiles(R is number, P is number, S is number, N is number, b is number, offset is number, res is number) returns map
{
    // scale so that the planet centres sit on the unit circle
    const f = S + P;
    var ring = rotatePoints(epiHypoGear(R / f, R, b, res), offset / R * PI);
    var planet = rotatePoints(epiHypoGear(P / f, P, b, res), offset / P * PI);
    var sun = rotatePoints(epiHypoGear(S / f, S, 1 - b, res), -offset / S * PI);
    // even planet tooth counts need the sun rotated by half a tooth for correct meshing
    if (P % 2 == 0)
    {
        sun = rotatePoints(sun, PI / S);
    }
    return { "ring" : ring, "planet" : planet, "sun" : sun };
}

function circlePoints(radius is number, n is number) returns array
{
    var pts = [];
    for (var i = 0; i < n; i += 1)
    {
        pts = append(pts, [radius * cos(2 * PI * i / n * radian), radius * sin(2 * PI * i / n * radian)]);
    }
    return pts;
}

function scalePoints(pts is array, scale is number) returns array
{
    var out = [];
    for (var i = 0; i < size(pts); i += 1)
    {
        out = append(out, [pts[i][0] * scale, pts[i][1] * scale]);
    }
    return out;
}

function maxRadius(pts is array) returns number
{
    var m = 0;
    for (var i = 0; i < size(pts); i += 1)
    {
        const r = sqrt(pts[i][0] * pts[i][0] + pts[i][1] * pts[i][1]);
        if (r > m)
        {
            m = r;
        }
    }
    return m;
}

// ---- builder begin ----------------------------------------------------------
// Turns a "model" (parts with planar faces in mm + placements) into solids.
//   part  : { name, faces : [{ outer : [[x,y],...], holes : [[[x,y],...]] }], internal, fuse, refRadius, hand }
//   inst  : { part, angle (radians), x, y (mm), layer }
//   opts  : { thickness, layerGap, toothType, helixAngle, layout, smooth }
// Twist convention (pygeartrain cad_export): the face at z = +thickness/2 is rotated by
// (thickness/2) * tan(helix) * hand / refRadius; helical: the -z face by the opposite angle,
// herringbone: by the same angle.  Mid-plane z = 0 carries the untwisted profile.

export enum ToothType
{
    annotation { "Name" : "Spur" }
    SPUR,
    annotation { "Name" : "Helical" }
    HELIX,
    annotation { "Name" : "Herringbone" }
    HERRINGBONE
}

export enum Layout
{
    annotation { "Name" : "Assembled (in mesh)" }
    ASSEMBLED,
    annotation { "Name" : "Parts in a row" }
    ROW
}

export const THICKNESS_BOUNDS = { (millimeter) : [0.1, 10, 1000] } as LengthBoundSpec;
export const GAP_BOUNDS = { (millimeter) : [0, 1, 1000] } as LengthBoundSpec;
export const HELIX_BOUNDS = { (degree) : [0, 20, 60] } as AngleBoundSpec;

function rotate2(p is array, angle is number) returns array
{
    const c = cos(angle * radian);
    const s = sin(angle * radian);
    return [p[0] * c - p[1] * s, p[0] * s + p[1] * c];
}

const SPLINE_SEGMENT_POINTS = 30;

function mmPoints(pts is array, rot is number) returns array
{
    var out = [];
    for (var i = 0; i < size(pts); i += 1)
    {
        out = append(out, vector(rotate2(pts[i], rot)) * millimeter);
    }
    return out;
}

// A closed loop is drawn as several fit splines sharing their end points.  A single
// closed spline cannot be lofted between rotated sections (its seam vertex has no
// counterpart), whereas a loop of ~30-point segments lofts cleanly and stays smooth.
function sketchLoops(context is Context, id is Id, loops is array, z is ValueWithUnits, rot is number, smooth is boolean) returns Query
{
    const pl = plane(vector(0 * millimeter, 0 * millimeter, z), vector(0, 0, 1), vector(1, 0, 0));
    var sk = newSketchOnPlane(context, id, { "sketchPlane" : pl });
    for (var li = 0; li < size(loops); li += 1)
    {
        const pts = mmPoints(loops[li], rot);
        const n = size(pts);
        if (!smooth)
        {
            skPolyline(sk, "loop" ~ li, { "points" : append(pts, pts[0]) });
            continue;
        }
        const segs = max(2, floor(n / SPLINE_SEGMENT_POINTS));
        for (var s = 0; s < segs; s += 1)
        {
            const start = floor(n * s / segs);
            const stop = floor(n * (s + 1) / segs); // inclusive; wraps to the first point on the last segment
            var seg = [];
            for (var i = start; i <= stop; i += 1)
            {
                seg = append(seg, pts[i % n]);
            }
            skFitSpline(sk, "loop" ~ li ~ "s" ~ s, { "points" : seg });
        }
    }
    skSolve(sk);
    return qSketchRegion(id, true);
}

function deleteSketch(context is Context, id is Id)
{
    opDeleteBodies(context, id + "del", { "entities" : qCreatedBy(id, EntityType.BODY) });
}

// A straight or twisted solid from one outer loop (and optional straight holes), mid-plane at z = 0.
function loopSolid(context is Context, id is Id, loops is array, halfTwist is number, thickness is ValueWithUnits,
                   toothType is ToothType, smooth is boolean) returns Query
{
    const h = thickness / 2;
    if (halfTwist == 0 || toothType == ToothType.SPUR)
    {
        const region = sketchLoops(context, id + "sk", loops, -h, 0, smooth);
        if (size(evaluateQuery(context, region)) == 0)
        {
            throw regenError("Sketch produced no closed region (" ~ size(loops) ~ " loops, " ~ size(loops[0]) ~ " points)");
        }
        opExtrude(context, id + "ex", {
            "entities" : region, "direction" : vector(0, 0, 1),
            "endBound" : BoundingType.BLIND, "endDepth" : thickness
        });
        deleteSketch(context, id + "sk");
        return qCreatedBy(id + "ex", EntityType.BODY);
    }
    if (toothType == ToothType.HELIX)
    {
        const r0 = sketchLoops(context, id + "skA", loops, -h, -halfTwist, smooth);
        const r1 = sketchLoops(context, id + "skB", loops, 0 * millimeter, 0, smooth);
        const r2 = sketchLoops(context, id + "skC", loops, h, halfTwist, smooth);
        try
        {
            opLoft(context, id + "loft", { "profileSubqueries" : [r0, r1, r2] });
        }
        catch (error)
        {
            throw regenError("Helical loft failed (" ~ size(loops[0]) ~ " points, twist " ~ halfTwist ~ " rad): " ~ toString(error));
        }
        deleteSketch(context, id + "skA");
        deleteSketch(context, id + "skB");
        deleteSketch(context, id + "skC");
        return qCreatedBy(id + "loft", EntityType.BODY);
    }
    // herringbone: two lofts meeting at the mid-plane
    const rLow = sketchLoops(context, id + "skA", loops, -h, halfTwist, smooth);
    const rMid = sketchLoops(context, id + "skB", loops, 0 * millimeter, 0, smooth);
    const rHigh = sketchLoops(context, id + "skC", loops, h, halfTwist, smooth);
    try
    {
        opLoft(context, id + "loftA", { "profileSubqueries" : [rLow, rMid] });
        opLoft(context, id + "loftB", { "profileSubqueries" : [rMid, rHigh] });
    }
    catch (error)
    {
        throw regenError("Herringbone loft failed (" ~ size(loops[0]) ~ " points, twist " ~ halfTwist ~ " rad): " ~ toString(error));
    }
    deleteSketch(context, id + "skA");
    deleteSketch(context, id + "skB");
    deleteSketch(context, id + "skC");
    opBoolean(context, id + "join", {
        "tools" : qUnion([qCreatedBy(id + "loftA", EntityType.BODY), qCreatedBy(id + "loftB", EntityType.BODY)]),
        "operationType" : BooleanOperationType.UNION
    });
    return qUnion([qCreatedBy(id + "loftA", EntityType.BODY), qCreatedBy(id + "loftB", EntityType.BODY)]);
}

function buildPart(context is Context, id is Id, part is map, opts is map) returns Query
{
    var halfTwist = 0;
    if (opts.toothType != ToothType.SPUR && part.refRadius > 0 && part.hand != 0)
    {
        halfTwist = ((opts.thickness / 2) / millimeter) * tan(opts.helixAngle) * part.hand / part.refRadius;
    }
    var bodies = [];
    for (var fi = 0; fi < size(part.faces); fi += 1)
    {
        const face = part.faces[fi];
        const fid = id + ("f" ~ fi);
        var body;
        if (part.internal)
        {
            // outer wall stays straight; the tooth loop(s) twist and are cut out
            body = loopSolid(context, fid + "wall", [face.outer], 0, opts.thickness, opts.toothType, false);
            var cutters = [];
            for (var hi = 0; hi < size(face.holes); hi += 1)
            {
                cutters = append(cutters, loopSolid(context, fid + ("cut" ~ hi), [face.holes[hi]], halfTwist, opts.thickness, opts.toothType, opts.smooth));
            }
            if (size(cutters) > 0)
            {
                opBoolean(context, fid + "cutBool", { "tools" : qUnion(cutters), "targets" : body, "operationType" : BooleanOperationType.SUBTRACTION });
            }
        }
        else if (halfTwist == 0 || opts.toothType == ToothType.SPUR)
        {
            // straight part: holes go into the same sketch
            body = loopSolid(context, fid + "solid", concatenateArrays([[face.outer], face.holes]), 0, opts.thickness, opts.toothType, opts.smooth);
        }
        else
        {
            // twisted outer loop, straight (bearing) holes
            body = loopSolid(context, fid + "solid", [face.outer], halfTwist, opts.thickness, opts.toothType, opts.smooth);
            var cutters = [];
            for (var hi = 0; hi < size(face.holes); hi += 1)
            {
                cutters = append(cutters, loopSolid(context, fid + ("hole" ~ hi), [face.holes[hi]], 0, opts.thickness, ToothType.SPUR, opts.smooth));
            }
            if (size(cutters) > 0)
            {
                opBoolean(context, fid + "holeBool", { "tools" : qUnion(cutters), "targets" : body, "operationType" : BooleanOperationType.SUBTRACTION });
            }
        }
        bodies = append(bodies, body);
    }
    if (part.fuse && size(bodies) > 1)
    {
        opBoolean(context, id + "fuse", { "tools" : qUnion(bodies), "operationType" : BooleanOperationType.UNION });
    }
    return qUnion(bodies);
}

function buildGearTrain(context is Context, id is Id, model is map, opts is map)
{
    var rowX = 0 * millimeter;
    for (var pi = 0; pi < size(model.parts); pi += 1)
    {
        const part = model.parts[pi];
        const pid = id + ("part" ~ pi);
        var body;
        try
        {
            body = buildPart(context, pid, part, opts);
        }
        catch (error)
        {
            throw regenError("Could not build part " ~ part.name ~ ": " ~ toString(error));
        }
        setProperty(context, { "entities" : body, "propertyType" : PropertyType.NAME, "value" : part.name });

        // instances of this part
        var transforms = [];
        var names = [];
        for (var ii = 0; ii < size(model.instances); ii += 1)
        {
            const inst = model.instances[ii];
            if (inst.part != part.name)
            {
                continue;
            }
            const z = inst.layer * (opts.thickness + opts.layerGap);
            var t;
            if (opts.layout == Layout.ASSEMBLED)
            {
                t = transform(vector(inst.x * millimeter, inst.y * millimeter, z)) *
                    rotationAround(line(vector(0, 0, 0) * millimeter, vector(0, 0, 1)), inst.angle * radian);
            }
            else
            {
                t = transform(vector(rowX, 0 * millimeter, 0 * millimeter));
            }
            transforms = append(transforms, t);
            names = append(names, "i" ~ ii);
            if (opts.layout == Layout.ROW)
            {
                break; // one copy per part in a row
            }
        }
        if (size(transforms) == 0)
        {
            transforms = [transform(vector(rowX, 0 * millimeter, 0 * millimeter))];
            names = ["i0"];
        }
        try
        {
            if (size(transforms) > 1)
            {
                opPattern(context, pid + "pat", {
                    "entities" : body,
                    "transforms" : subArray(transforms, 1, size(transforms)),
                    "instanceNames" : subArray(names, 1, size(names))
                });
            }
            opTransform(context, pid + "move", { "bodies" : body, "transform" : transforms[0] });
        }
        catch (error)
        {
            throw regenError("Could not place part " ~ part.name ~ ": " ~ toString(error));
        }
        rowX = rowX + 2 * part.refRadius * (1 + (part.internal ? 0.15 : 0)) * millimeter + opts.thickness;
    }
}
// ---- builder end ------------------------------------------------------------
