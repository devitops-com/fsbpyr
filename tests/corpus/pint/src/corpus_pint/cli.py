"""pint and uncertainties: a unit registry read from the definitions file of pint, contexts,
measurements, error propagation, and the constants table of scipy."""

import numpy as np
import pint
from scipy import constants
from uncertainties import ufloat, umath, unumpy


def units_part() -> None:
    ureg = pint.UnitRegistry()
    distance = (26.2 * ureg.mile).to(ureg.km)
    print("convert", round(distance.magnitude, 4), distance.units)
    print("dimensionality", (5 * ureg.newton).dimensionality,
          round((3 * ureg.psi).to("kPa").magnitude, 3))
    speed = (100 * ureg.km / ureg.hour).to_base_units()
    print("base units", round(speed.magnitude, 4), speed.units)
    with ureg.context("spectroscopy"):
        print("spectroscopy", round((500 * ureg.nm).to("THz").magnitude, 3))
    arrays = np.array([1.0, 2.5, 4.0]) * ureg.meter
    print("array", (arrays / (2 * ureg.second)).to("cm/s").magnitude.tolist(), arrays.sum())
    measured = ureg.Measurement(12.0, 0.5, "meter")
    print("measurement", round((measured * 2).value.magnitude, 3),
          round((measured * 2).error.magnitude, 3))
    try:
        (1 * ureg.meter).to("second")
    except pint.DimensionalityError as error:
        print("dimensionality error", error)


def uncertainties_part() -> None:
    x = ufloat(2.0, 0.1)
    y = ufloat(3.0, 0.2)
    print("sum", f"{x + y:.2uf}", "product", f"{x * y:.2uf}")
    print("umath", f"{umath.sin(x):.3uf}", f"{umath.sqrt(y):.3uf}")
    values = unumpy.uarray([1.0, 2.0], [0.1, 0.2])
    print("uarray", unumpy.nominal_values(values * 2).tolist(),
          unumpy.std_devs(values * 2).tolist())


def constants_part() -> None:
    value, unit, uncertainty = constants.physical_constants["electron mass"]
    print("electron mass", value, unit, uncertainty)
    print("constants", constants.value("Planck constant"),
          constants.unit("speed of light in vacuum"))
    print("precision", constants.precision("proton mass"))


def main() -> None:
    units_part()
    uncertainties_part()
    constants_part()
