"""h5py, PyTables (blosc2 found through ctypes) and netCDF4 with cftime: native I/O libraries."""

import tempfile
from pathlib import Path

import cftime
import h5py
import netCDF4
import numpy as np
import tables


def with_h5py(directory: Path) -> None:
    data = np.arange(1000, dtype="f8").reshape(10, 100)
    with h5py.File(directory / "a.h5", "w") as file:
        file.create_dataset("gzip", data=data, compression="gzip", compression_opts=4,
                            chunks=(5, 50))
        file.create_dataset("lzf", data=data, compression="lzf", chunks=(5, 50))
        file["gzip"].attrs["units"] = "metres"
        file.create_group("meta").attrs["version"] = 3
    with h5py.File(directory / "a.h5") as file:
        print("h5py", sorted(file), file["gzip"].compression, file["lzf"].compression,
              file["gzip"].attrs["units"])
        print("h5py sums", float(file["gzip"][:].sum()), float(file["lzf"][2:4].sum()),
              int(file["meta"].attrs["version"]))


class Row(tables.IsDescription):
    ident = tables.Int32Col()
    value = tables.Float64Col()


def with_pytables(directory: Path) -> None:
    filters = tables.Filters(complevel=5, complib="blosc2:zstd")
    with tables.open_file(directory / "t.h5", "w", filters=filters) as file:
        table = file.create_table("/", "rows", Row)
        table.append([(i, i * 0.5) for i in range(500)])
        table.flush()
    with tables.open_file(directory / "t.h5") as file:
        table = file.root.rows
        print("pytables", table.nrows, table.filters.complib, float(table.col("value").sum()))


def with_netcdf(directory: Path) -> None:
    with netCDF4.Dataset(directory / "n.nc", "w") as dataset:
        dataset.createDimension("time", None)
        dataset.createDimension("x", 4)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "days since 2000-01-01"
        time.calendar = "standard"
        field = dataset.createVariable("field", "f4", ("time", "x"), zlib=True, complevel=4)
        time[:] = [0, 31, 60]
        field[:] = np.arange(12, dtype="f4").reshape(3, 4)
    with netCDF4.Dataset(directory / "n.nc") as dataset:
        times = cftime.num2date(dataset["time"][:], dataset["time"].units, dataset["time"].calendar)
        print("netcdf", dataset.dimensions["time"].size, dataset["field"].filters()["zlib"],
              [t.isoformat() for t in times])
        print("netcdf sum", float(dataset["field"][:].sum()))


def main() -> None:
    with tempfile.TemporaryDirectory() as name:
        directory = Path(name)
        with_h5py(directory)
        with_pytables(directory)
        with_netcdf(directory)
