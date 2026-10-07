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
 * by the number of planets for equal spacing.  The feature reports the gear
 * ratio for the chosen input / output / fixed members, the module, pitch and
 * outer diameters and the planet-centre circle in its info message.
 */

export enum SizeBy
{
    annotation { "Name" : "Module (mm per tooth)" }
    MODULE,
    annotation { "Name" : "Planet centre circle diameter" }
    PLANET_CIRCLE,
    annotation { "Name" : "Ring tooth tip diameter" }
    RING_TEETH
}

export enum Member
{
    annotation { "Name" : "Sun (stage 1)" }
    SUN,
    annotation { "Name" : "Carrier" }
    CARRIER,
    annotation { "Name" : "Ring (stage 1)" }
    RING,
    annotation { "Name" : "Sun 2" }
    SUN2,
    annotation { "Name" : "Ring 2" }
    RING2
}

export const TEETH_BOUNDS = { (unitless) : [1, 12, 400] } as IntegerBoundSpec;
export const PLANET_COUNT_BOUNDS = { (unitless) : [1, 3, 40] } as IntegerBoundSpec;
export const MIX_BOUNDS = { (unitless) : [0.05, 0.5, 0.95] } as RealBoundSpec;
export const SIZE_BOUNDS = { (millimeter) : [0.01, 2, 5000] } as LengthBoundSpec;
export const RING_OUTER_BOUNDS = { (millimeter) : [1, 80, 5000] } as LengthBoundSpec;
export const BORE_BOUNDS = { (millimeter) : [0, 0, 1000] } as LengthBoundSpec;
export const CLEARANCE_BOUNDS = { (millimeter) : [0, 0, 10] } as LengthBoundSpec;
export const MARGIN_BOUNDS = { (unitless) : [0.01, 0.12, 2] } as RealBoundSpec;
export const RES_BOUNDS = { (unitless) : [100, 500, 2000] } as IntegerBoundSpec;

annotation { "Feature Type Name" : "Cycloidal planetary",
             "Feature Type Description" : "Single or compound planetary gear train with cycloidal teeth (pygeartrain)" }
export const cycloidalPlanetary = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Group Name" : "Teeth", "Collapsed By Default" : false }
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
        }

        annotation { "Group Name" : "Size", "Collapsed By Default" : false }
        {
            annotation { "Name" : "Size by" }
            definition.sizeBy is SizeBy;
            annotation { "Name" : "Size" }
            isLength(definition.size, SIZE_BOUNDS);
            annotation { "Name" : "Set ring outer diameter" }
            definition.setRingOuter is boolean;
            if (definition.setRingOuter)
            {
                annotation { "Name" : "Ring outer diameter" }
                isLength(definition.ringOuterDiameter, RING_OUTER_BOUNDS);
            }
            else
            {
                annotation { "Name" : "Ring wall (fraction of tooth radius)" }
                isReal(definition.ringMargin, MARGIN_BOUNDS);
            }
            annotation { "Name" : "Face width (thickness)" }
            isLength(definition.thickness, THICKNESS_BOUNDS);
        }

        annotation { "Group Name" : "Teeth shape", "Collapsed By Default" : false }
        {
            annotation { "Name" : "Tooth type" }
            definition.toothType is ToothType;
            if (definition.toothType != ToothType.SPUR)
            {
                annotation { "Name" : "Helix angle" }
                isAngle(definition.helixAngle, HELIX_BOUNDS);
            }
            annotation { "Name" : "Tooth clearance (total backlash)" }
            isLength(definition.clearance, CLEARANCE_BOUNDS);
            annotation { "Name" : "Profile resolution" }
            isInteger(definition.resolution, RES_BOUNDS);
        }

        annotation { "Group Name" : "Bores", "Collapsed By Default" : false }
        {
            annotation { "Name" : "Sun bore diameter (0 = none)" }
            isLength(definition.sunBore, BORE_BOUNDS);
            annotation { "Name" : "Planet bore diameter (0 = none)" }
            isLength(definition.planetBore, BORE_BOUNDS);
        }

        annotation { "Group Name" : "Kinematics (for the reported ratio)", "Collapsed By Default" : false }
        {
            annotation { "Name" : "Input", "Default" : "SUN" }
            definition.inputMember is Member;
            annotation { "Name" : "Output", "Default" : "CARRIER" }
            definition.outputMember is Member;
            annotation { "Name" : "Fixed", "Default" : "RING" }
            definition.fixedMember is Member;
        }

        annotation { "Name" : "Layout" }
        definition.layout is Layout;
    }
    {
        try
        {
            featureBody(context, id, definition);
        }
        catch (error)
        {
            throw regenError("Cycloidal planetary: " ~ toString(error));
        }
    });

function featureBody(context is Context, id is Id, definition is map)
{
    var stage = "start";
    try
    {
        const stages = definition.compound ? 2 : 1;
        var model = { "parts" : [], "instances" : [] };
        var scale = 1.0; // mm per library unit (planet centres sit at radius 1)
        var info = [];
        for (var s = 0; s < stages; s += 1)
        {
            const R = s == 0 ? definition.ringTeeth : definition.ringTeeth2;
            const P = s == 0 ? definition.planetTeeth : definition.planetTeeth2;
            const S = s == 0 ? definition.sunTeeth : definition.sunTeeth2;
            const b = s == 0 ? definition.mix : definition.mix2;
            const N = definition.planetCount;
            if (R != S + 2 * P)
            {
                reportFeatureWarning(context, id, "Stage " ~ (s + 1) ~ ": R should equal S + 2P (" ~ (S + 2 * P) ~ ") for the planets to fit.");
            }
            if ((R + S) % N != 0)
            {
                reportFeatureWarning(context, id, "Stage " ~ (s + 1) ~ ": (R + S) is not divisible by N; planets cannot be equally spaced.");
            }
            // second stage profiles are offset by half a planet tooth, as in pygeartrain
            const offset = s == 0 ? 0 : 0.5 * P;
            stage = "profiles of stage " ~ (s + 1);
            const prof = planetaryProfiles(R, P, S, N, b, offset, definition.resolution);
            stage = "sizing";
            if (s == 0)
            {
                const sizeMm = definition.size / millimeter;
                if (definition.sizeBy == SizeBy.MODULE)
                {
                    scale = sizeMm * (S + P) / 2;
                }
                else if (definition.sizeBy == SizeBy.PLANET_CIRCLE)
                {
                    scale = sizeMm / 2;
                }
                else
                {
                    scale = (sizeMm / 2) / maxRadius(prof.ring);
                }
            }
            const prefix = stages == 1 ? "" : ("stage" ~ (s + 1) ~ "_");
            const names = ["ring", "planet", "sun"];
            const hands = [-1, -1, 1];
            const loops = [prof.ring, prof.planet, prof.sun];
            const counts = [R, P, S];
            const bores = [0, definition.planetBore / millimeter, definition.sunBore / millimeter];
            stage = "info line";
            const module = 2 * scale / (S + P);
            var line = (stages == 1 ? "" : ("Stage " ~ (s + 1) ~ ": ")) ~ "module " ~ roundToPrecision(module, 4) ~ " mm";
            line = line ~ ", pitch dia ring " ~ roundToPrecision(2 * R / (S + P) * scale, 3) ~ " / planet " ~ roundToPrecision(2 * P / (S + P) * scale, 3) ~ " / sun " ~ roundToPrecision(2 * S / (S + P) * scale, 3);
            stage = "faces";
            for (var k = 0; k < 3; k += 1)
            {
                const pts = scalePoints(loops[k], scale);
                const rMax = maxRadius(pts);
                line = line ~ ", " ~ names[k] ~ " outer dia " ~ roundToPrecision(2 * rMax, 3);
                var faces;
                if (k == 0)
                {
                    // ring gear: a true circle wall with the tooth loop as a hole
                    // parameters hidden by the precondition are absent from `definition`
                    var rWall;
                    if (definition.setRingOuter)
                    {
                        rWall = definition.ringOuterDiameter / millimeter / 2;
                        if (rWall <= rMax + 0.05)
                        {
                            throw regenError("Ring outer diameter must exceed the ring tooth tip diameter of " ~ roundToPrecision(2 * rMax, 3) ~ " mm.");
                        }
                    }
                    else
                    {
                        rWall = rMax * (1 + definition.ringMargin);
                    }
                    faces = [{ "outer" : { "circle" : rWall }, "holes" : [pts] }];
                }
                else
                {
                    var holes = [];
                    if (bores[k] > 0)
                    {
                        holes = [{ "circle" : bores[k] / 2 }];
                    }
                    faces = [{ "outer" : pts, "holes" : holes }];
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
            stage = "instances";
            info = append(info, line);
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

        // ratio for the chosen members
        stage = "ratio";
        const ratio = gearRatio(definition);
        var head = "Ratio " ~ memberName(definition.inputMember) ~ " / " ~ memberName(definition.outputMember) ~ " = " ~ roundToPrecision(ratio, 4) ~ " : 1";
        head = head ~ " (" ~ memberName(definition.fixedMember) ~ " fixed). Planet centre circle dia " ~ roundToPrecision(2 * scale, 3) ~ " mm";
        stage = "report";
        reportFeatureInfo(context, id, head ~ ". " ~ joinStrings(info, ". "));
        stage = "build";

        buildGearTrain(context, id, model, {
            "thickness" : definition.thickness,
            "layerGap" : definition.compound ? definition.stageGap : 0 * millimeter,
            "toothType" : definition.toothType,
            "helixAngle" : definition.toothType == ToothType.SPUR ? 0 * degree : definition.helixAngle,
            "layout" : definition.layout,
            "clearance" : definition.clearance,
            "smooth" : true
        });
    }
    catch (error)
    {
        throw regenError("at " ~ stage ~ ": " ~ toString(error));
    }
}

// ---------------------------------------------------------------------------
// Kinematics: solve the meshing equations of pygeartrain for the chosen members
// unknowns single stage: [s, p, c, r]; compound: [s1, r1, s2, r2, c, p]
// ---------------------------------------------------------------------------

function joinStrings(parts is array, separator is string) returns string
{
    var out = "";
    for (var i = 0; i < size(parts); i += 1)
    {
        out = out ~ (i == 0 ? "" : separator) ~ parts[i];
    }
    return out;
}

function memberName(m is Member) returns string
{
    if (m == Member.SUN)
    {
        return "sun";
    }
    if (m == Member.CARRIER)
    {
        return "carrier";
    }
    if (m == Member.RING)
    {
        return "ring";
    }
    if (m == Member.SUN2)
    {
        return "sun 2";
    }
    return "ring 2";
}

function memberIndex(m is Member, compound is boolean) returns number
{
    if (!compound)
    {
        if (m == Member.SUN)
        {
            return 0;
        }
        if (m == Member.CARRIER)
        {
            return 2;
        }
        if (m == Member.RING)
        {
            return 3;
        }
        throw regenError("Sun 2 / Ring 2 only exist on a compound planetary.");
    }
    if (m == Member.SUN)
    {
        return 0;
    }
    if (m == Member.RING)
    {
        return 1;
    }
    if (m == Member.SUN2)
    {
        return 2;
    }
    if (m == Member.RING2)
    {
        return 3;
    }
    return 4; // carrier
}

function gearRatio(definition is map) returns number
{
    const R = definition.ringTeeth;
    const P = definition.planetTeeth;
    const S = definition.sunTeeth;
    if (definition.inputMember == definition.outputMember || definition.inputMember == definition.fixedMember || definition.outputMember == definition.fixedMember)
    {
        throw regenError("Input, output and fixed members must all be different.");
    }
    var rows = [];
    var n;
    if (!definition.compound)
    {
        n = 4;
        rows = [[S, P, -(S + P), 0], [0, -P, -(R - P), R]];
    }
    else
    {
        n = 6;
        const R2 = definition.ringTeeth2;
        const P2 = definition.planetTeeth2;
        const S2 = definition.sunTeeth2;
        rows = [[S, 0, 0, 0, -(S + P), P], [0, R, 0, 0, -(R - P), -P],
                [0, 0, S2, 0, -(S2 + P2), P2], [0, 0, 0, R2, -(R2 - P2), -P2]];
    }
    var rhs = makeArray(size(rows), 0);
    var fixedRow = makeArray(n, 0);
    fixedRow[memberIndex(definition.fixedMember, definition.compound)] = 1;
    var outRow = makeArray(n, 0);
    outRow[memberIndex(definition.outputMember, definition.compound)] = 1;
    rows = append(rows, fixedRow);
    rows = append(rows, outRow);
    rhs = append(rhs, 0);
    rhs = append(rhs, 1);
    const sol = solveLinear(rows, rhs);
    return sol[memberIndex(definition.inputMember, definition.compound)];
}

function solveLinear(A is array, b is array) returns array
{
    // Gaussian elimination with partial pivoting; A is n x n (array of rows)
    const n = size(b);
    var M = A;
    var y = b;
    for (var col = 0; col < n; col += 1)
    {
        var piv = col;
        for (var r = col + 1; r < n; r += 1)
        {
            if (abs(M[r][col]) > abs(M[piv][col]))
            {
                piv = r;
            }
        }
        if (abs(M[piv][col]) < 1e-12)
        {
            throw regenError("The chosen members do not determine a ratio.");
        }
        if (piv != col)
        {
            const tmpRow = M[col];
            M[col] = M[piv];
            M[piv] = tmpRow;
            const tmp = y[col];
            y[col] = y[piv];
            y[piv] = tmp;
        }
        for (var r = 0; r < n; r += 1)
        {
            if (r == col)
            {
                continue;
            }
            const f = M[r][col] / M[col][col];
            if (f != 0)
            {
                var row = M[r];
                for (var k = col; k < n; k += 1)
                {
                    row[k] = row[k] - f * M[col][k];
                }
                M[r] = row;
                y[r] = y[r] - f * y[col];
            }
        }
    }
    var x = makeArray(n, 0);
    for (var i = 0; i < n; i += 1)
    {
        x[i] = y[i] / M[i][i];
    }
    return x;
}

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
//   part  : { name, faces : [{ outer : loop, holes : [loop, ...] }], internal, fuse, refRadius, hand }
//   loop  : [[x, y], ...] (closed polygon, mm) or { "circle" : radius_mm } centred on the part axis
//   inst  : { part, angle (radians), x, y (mm), layer }
//   opts  : { thickness, layerGap, toothType, helixAngle, layout, smooth, clearance }
// Twist convention (pygeartrain cad_export): the face at z = +thickness/2 is rotated by
// (thickness/2) * tan(helix) * hand / refRadius; helical: the -z face by the opposite angle,
// herringbone: by the same angle.  Mid-plane z = 0 carries the untwisted profile.
// Clearance: every gear tooth loop (hand != 0) is offset by clearance/2, inwards for external
// gears and outwards for the tooth hole of an internal gear, giving `clearance` of backlash.

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
export const BACKLASH_BOUNDS = { (millimeter) : [0, 0, 10] } as LengthBoundSpec;
const SPLINE_SEGMENT_POINTS = 30;

function rotate2(p is array, angle is number) returns array
{
    const c = cos(angle * radian);
    const s = sin(angle * radian);
    return [p[0] * c - p[1] * s, p[0] * s + p[1] * c];
}

function signedArea(pts is array) returns number
{
    var a = 0;
    const n = size(pts);
    for (var i = 0; i < n; i += 1)
    {
        const p = pts[i];
        const q = pts[(i + 1) % n];
        a += p[0] * q[1] - q[0] * p[1];
    }
    return a / 2;
}

// Offset a closed polygon outwards by d (inwards for negative d) along vertex normals.
function offsetLoop(pts is array, d is number) returns array
{
    const n = size(pts);
    if (n < 3 || d == 0)
    {
        return pts;
    }
    const sign = signedArea(pts) > 0 ? 1 : -1; // outward normal of a CCW loop is (dy, -dx)
    var out = [];
    for (var i = 0; i < n; i += 1)
    {
        const prev = pts[(i + n - 1) % n];
        const next = pts[(i + 1) % n];
        var nx = (next[1] - prev[1]) * sign;
        var ny = -(next[0] - prev[0]) * sign;
        const len = sqrt(nx * nx + ny * ny);
        if (len > 0)
        {
            nx = nx / len;
            ny = ny / len;
        }
        out = append(out, [pts[i][0] + d * nx, pts[i][1] + d * ny]);
    }
    return out;
}

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
        if (loops[li] is map)
        {
            skCircle(sk, "loop" ~ li, { "center" : vector(0, 0) * millimeter, "radius" : loops[li].circle * millimeter });
            continue;
        }
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

// The delete op must not be a child of the sketch id: Onshape rejects a parent id that is
// used again after another operation ("used at two non-contiguous points").
function deleteSketch(context is Context, opId is Id, sketchId is Id)
{
    opDeleteBodies(context, opId, { "entities" : qCreatedBy(sketchId, EntityType.BODY) });
}

function loopPointCount(loops is array) returns number
{
    return loops[0] is map ? 0 : size(loops[0]);
}

// A straight or twisted solid from one outer loop (and optional straight holes), mid-plane at z = 0.
function loopSolid(context is Context, id is Id, loops is array, halfTwist is number, thickness is ValueWithUnits,
                   toothType is ToothType, smooth is boolean) returns Query
{
    const h = thickness / 2;
    if (halfTwist == 0 || toothType == ToothType.SPUR || loops[0] is map)
    {
        const region = sketchLoops(context, id + "sk", loops, -h, 0, smooth);
        if (size(evaluateQuery(context, region)) == 0)
        {
            throw regenError("Sketch produced no closed region (" ~ size(loops) ~ " loops, " ~ loopPointCount(loops) ~ " points)");
        }
        opExtrude(context, id + "ex", {
            "entities" : region, "direction" : vector(0, 0, 1),
            "endBound" : BoundingType.BLIND, "endDepth" : thickness
        });
        deleteSketch(context, id + "delSk", id + "sk");
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
            throw regenError("Helical loft failed (" ~ loopPointCount(loops) ~ " points, twist " ~ halfTwist ~ " rad): " ~ toString(error));
        }
        deleteSketch(context, id + "delA", id + "skA");
        deleteSketch(context, id + "delB", id + "skB");
        deleteSketch(context, id + "delC", id + "skC");
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
        throw regenError("Herringbone loft failed (" ~ loopPointCount(loops) ~ " points, twist " ~ halfTwist ~ " rad): " ~ toString(error));
    }
    deleteSketch(context, id + "delA", id + "skA");
    deleteSketch(context, id + "delB", id + "skB");
    deleteSketch(context, id + "delC", id + "skC");
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
    const halfClearance = (opts.clearance == undefined ? 0 : opts.clearance / millimeter) / 2;
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
                var hole = face.holes[hi];
                if (!(hole is map) && part.hand != 0)
                {
                    hole = offsetLoop(hole, halfClearance); // enlarge the tooth hole
                }
                cutters = append(cutters, loopSolid(context, fid + ("cut" ~ hi), [hole], halfTwist, opts.thickness, opts.toothType, opts.smooth));
            }
            if (size(cutters) > 0)
            {
                opBoolean(context, fid + "cutBool", { "tools" : qUnion(cutters), "targets" : body, "operationType" : BooleanOperationType.SUBTRACTION });
            }
        }
        else
        {
            var outer = face.outer;
            if (!(outer is map) && part.hand != 0)
            {
                outer = offsetLoop(outer, -halfClearance); // shrink the external tooth loop
            }
            if (halfTwist == 0 || opts.toothType == ToothType.SPUR)
            {
                // straight part: holes go into the same sketch
                body = loopSolid(context, fid + "solid", concatenateArrays([[outer], face.holes]), 0, opts.thickness, opts.toothType, opts.smooth);
            }
            else
            {
                // twisted outer loop, straight (bearing) holes
                body = loopSolid(context, fid + "solid", [outer], halfTwist, opts.thickness, opts.toothType, opts.smooth);
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
