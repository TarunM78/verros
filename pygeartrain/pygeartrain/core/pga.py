"""Rigid 2D motions as motors of the 2D projective geometric algebra (PGA).

The algebra is P(R*_{2,0,1}) with basis w (w*w = 0), x, y (x*x = y*y = 1).
Motors live in the even subalgebra spanned by (1, wx, wy, xy); a motor acts on
points by the sandwich product.  This is a small hand-written implementation of
exactly that subalgebra, so that it needs nothing but numpy; it reproduces the
conventions of the numga-based original (see test_pga below, which checks
against numga when it is installed).

Conventions
-----------
rotor(a)          rotates counter-clockwise by angle a (radians)
translator(tx,ty) translates by (tx, ty)
A * B             composition; applying A*B means applying B first, then A
A >> B            sandwich A B ~A: the motor B carried along by the motion A
A << B            reverse sandwich ~A B A
transform(M, p)   apply motor M to an (n, 2) array of points
"""
import numpy as np


class Motor:
    """Even multivector (1, wx, wy, xy) of 2D PGA; a rigid motion."""
    __slots__ = ('v',)

    def __init__(self, values):
        self.v = np.asarray(values, dtype=float)

    # -- algebra ---------------------------------------------------------------
    def __mul__(self, other):
        if not isinstance(other, Motor):
            return Motor(self.v * float(other))
        a0, a1, a2, a3 = self.v
        b0, b1, b2, b3 = other.v
        return Motor((
            a0 * b0 - a3 * b3,
            a0 * b1 + a1 * b0 + a3 * b2 - a2 * b3,
            a0 * b2 + a2 * b0 + a1 * b3 - a3 * b1,
            a0 * b3 + a3 * b0,
        ))

    def __rmul__(self, other):
        return Motor(self.v * float(other))

    def __add__(self, other):
        other = other if isinstance(other, Motor) else Motor((float(other), 0, 0, 0))
        return Motor(self.v + other.v)

    __radd__ = __add__

    def reverse(self):
        return Motor(self.v * (1, -1, -1, -1))

    def __invert__(self):
        return self.reverse()

    def __rshift__(self, other):
        """self >> other: sandwich self * other * ~self"""
        return self * other * self.reverse()

    def __lshift__(self, other):
        """self << other: reverse sandwich ~self * other * self"""
        return self.reverse() * other * self

    # -- as a rigid motion ---------------------------------------------------------
    def rotation_matrix_and_translation(self):
        """Return (R, t) with p' = p @ R.T + t for row-vector points p."""
        s, a, b, c = self.v
        n = s * s + c * c
        cos_t = (s * s - c * c) / n
        sin_t = -2 * s * c / n
        tx = -2 * (a * s + b * c) / n
        ty = 2 * (a * c - b * s) / n
        R = np.array([[cos_t, -sin_t], [sin_t, cos_t]])
        return R, np.array([tx, ty])

    def __repr__(self):
        return f'Motor(1:{self.v[0]:.4g}, wx:{self.v[1]:.4g}, wy:{self.v[2]:.4g}, xy:{self.v[3]:.4g})'


def rotor(angle):
    """Rotation about the origin by `angle` radians, counter-clockwise."""
    half = -0.5 * float(angle)
    return Motor((np.cos(half), 0.0, 0.0, np.sin(half)))


def translator(tx, ty):
    """Translation by (tx, ty)."""
    return Motor((1.0, -0.5 * float(tx), -0.5 * float(ty), 0.0))


def transform(motor, p):
    """Apply motor to an (n, 2) array of points."""
    R, t = motor.rotation_matrix_and_translation()
    return np.asarray(p, dtype=float) @ R.T + t


def test_pga():
    """Compare against the numga implementation this module replaced."""
    import pytest
    numga = pytest.importorskip('numga')
    from numga.backend.numpy.context import NumpyContext

    pga = NumpyContext('w0x+y+')
    xy, wx, wy = pga.multivector.xy, pga.multivector.wx, pga.multivector.wy

    def n_rotor(angle):
        return xy * np.sin(angle / -2) + np.cos(angle / -2)

    def n_translator(tx, ty):
        return 1 + wx * tx / -2 + wy * ty / -2

    signs = 1 - (np.arange(9).reshape(3, 3) % 2) * 2

    def n_transform(motor, p):
        m = (motor.sandwich(pga.subspace.antivector()).kernel * signs)[::-1, ::-1]
        return p.dot(m[1:, 1:]) + m[0:1, 1:]

    def full(m):
        """numga motor values in (1, wx, wy, xy) order"""
        return m.select_subspace(pga.subspace.even_grade()).values

    rng = np.random.default_rng(0)
    pts = rng.normal(size=(7, 2))
    for _ in range(50):
        a, b, c = rng.normal(size=3) * 3
        tx, ty, ux, uy = rng.normal(size=4) * 2
        ours = (rotor(a) >> translator(tx, ty)) * rotor(b) * (translator(ux, uy) << rotor(c))
        theirs = (n_rotor(a) >> n_translator(tx, ty)) * n_rotor(b) * (n_translator(ux, uy) << n_rotor(c))
        np.testing.assert_allclose(ours.v, full(theirs), atol=1e-12)
        np.testing.assert_allclose(transform(ours, pts), n_transform(theirs, pts), atol=1e-12)
        np.testing.assert_allclose(transform(ours.reverse(), pts), n_transform(theirs.reverse(), pts), atol=1e-12)
    # plain sanity, independent of numga
    np.testing.assert_allclose(transform(rotor(np.pi / 2), np.array([[1.0, 0.0]])), [[0.0, 1.0]], atol=1e-12)
    np.testing.assert_allclose(transform(translator(2, 3), np.array([[1.0, 0.0]])), [[3.0, 3.0]], atol=1e-12)
