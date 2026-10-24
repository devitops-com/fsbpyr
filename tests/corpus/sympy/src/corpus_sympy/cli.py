"""sympy's symbolic engine: solving, calculus, lambdify (generated source), printing, mpmath."""

import flint
import gmpy2
import mpmath
import numpy as np
import sympy
from sympy import (
    Eq,
    Matrix,
    Symbol,
    cos,
    exp,
    integrate,
    lambdify,
    latex,
    pprint,
    series,
    sin,
    solve,
)
from sympy.external import gmpy as sympy_gmpy

x = Symbol("x")


def ground_types() -> str:
    """Which integer/rational backend sympy picked (SYMPY_GROUND_TYPES), as its version names it."""
    return str(sympy_gmpy.GROUND_TYPES)


def main() -> None:
    print("ground types", ground_types())
    print("gmpy2", gmpy2.powmod(3, 200, 10**9 + 7), "flint", flint.fmpz(2) ** 100 % 10**9)
    print("solve", solve(Eq(x**2 - 5 * x + 6, 0), x))
    print("integrate", integrate(x * exp(-x), (x, 0, sympy.oo)), integrate(sin(x) ** 2, x))
    print("series", series(cos(x), x, 0, 6))
    print("simplify", sympy.simplify((x**2 - 1) / (x - 1)), sympy.factor(x**4 - 1))
    print("eigenvals", sorted(Matrix([[2, 1], [1, 2]]).eigenvals().items()))
    print("rational", sympy.Rational(3, 7) + sympy.Rational(2, 5), sympy.factorint(2**32 + 1))

    expr = sin(x) * x**2 + 1
    function = lambdify(x, expr, modules="numpy")  # compiles generated source
    print("lambdify", np.round(function(np.linspace(0.0, 2.0, 5)), 6).tolist())
    print("latex", latex(sympy.Integral(x**2, (x, 0, 1))))
    print("pretty:")
    pprint(sympy.Integral(sympy.sqrt(x) / (1 + x), x), use_unicode=True, wrap_line=False)

    mpmath.mp.dps = 50
    print("mpmath pi", mpmath.pi)
    print("mpmath zeta", mpmath.zeta(3))
    print("sympy N", sympy.N(sympy.sqrt(2), 40))
