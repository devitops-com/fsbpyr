"""astropy: units, time scales with the IERS tables of astropy-iers-data, coordinates, the
packaged cosmology data, FITS and ECSV files."""

import os
import tempfile
from pathlib import Path


def units_part() -> None:
    from astropy import constants
    from astropy import units as u

    speed = (1 * u.au / (8.3167 * u.min)).to(u.km / u.s)
    print("units", round(speed.value, 1), speed.unit)
    print("constants", constants.c.value, round(constants.G.value, 14), constants.h.unit)
    print("equivalency", round((500 * u.nm).to(u.THz, u.spectral()).value, 3))


def time_part() -> None:
    from astropy import units as u
    from astropy.time import Time

    t = Time("2020-01-01T00:00:00", scale="utc")
    print("time", t.tt.iso, t.tdb.iso, t.tai.iso)
    print("jd", t.jd, round((t + 1 * u.day).mjd, 3))
    print("ut1", round(float(t.ut1.jd2), 6))  # reads the IERS tables


def coordinates_part() -> None:
    from astropy import units as u
    from astropy.coordinates import SkyCoord

    galactic = SkyCoord(ra=10.68458 * u.deg, dec=41.26917 * u.deg, frame="icrs").galactic
    print("galactic", round(galactic.l.deg, 3), round(galactic.b.deg, 3))
    other = SkyCoord(3 * u.deg, 4 * u.deg)
    print("separation", round(SkyCoord(0 * u.deg, 0 * u.deg).separation(other).deg, 4))


def cosmology_part() -> None:
    from astropy.cosmology import Planck18

    print("planck18", Planck18.name, round(Planck18.H0.value, 2))
    print("distances", round(Planck18.luminosity_distance(0.5).value, 2),
          round(Planck18.comoving_distance(1.0).value, 2))  # fmt: skip


def files_part(root: Path) -> None:
    import numpy as np
    from astropy import units as u
    from astropy.io import fits
    from astropy.table import Table

    image = fits.PrimaryHDU(np.arange(6, dtype="int16").reshape(2, 3))
    image.header["OBJECT"] = "corpus"
    catalog = fits.BinTableHDU(Table({"x": [1, 2], "y": [0.5, 1.5]}), name="CAT")
    fits.HDUList([image, catalog]).writeto(root / "a.fits")
    with fits.open(root / "a.fits") as hdus:
        print("fits", [h.name for h in hdus], hdus[0].header["OBJECT"], int(hdus[0].data.sum()),
              hdus["CAT"].data["y"].tolist())  # fmt: skip
    table = Table({"name": ["a", "b"], "flux": [1.5, 2.5] * u.Jy})
    table.write(root / "t.ecsv")
    back = Table.read(root / "t.ecsv")
    print("ecsv", back["flux"].unit, back["flux"].tolist(), back["name"].tolist())


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        # astropy writes its configuration and cache below these: set before the import
        os.environ["XDG_CONFIG_HOME"] = os.environ["XDG_CACHE_HOME"] = directory
        from astropy.utils import iers

        iers.conf.auto_download = False  # the tables of astropy-iers-data, no download
        units_part()
        time_part()
        coordinates_part()
        cosmology_part()
        files_part(Path(directory))
