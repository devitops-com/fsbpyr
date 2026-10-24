"""xarray on dask and zarr: labelled arrays, entry-point backends and chunk managers, and
dask.distributed worker processes."""

import logging
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


def dataset() -> xr.Dataset:
    time = pd.date_range("2024-01-01", periods=12, freq="D")
    values = np.arange(12 * 3, dtype="float64").reshape(12, 3)
    return xr.Dataset(
        {"temperature": (("time", "site"), values)},
        coords={"time": time, "site": ["a", "b", "c"]},
    )


def labelled(ds: xr.Dataset) -> None:
    print("sizes", dict(ds.sizes), "site mean", ds.temperature.mean("time").values.tolist())
    print("groupby", ds.temperature.groupby("time.day").mean().isel(day=0).values.tolist()[:3])
    print("resample", ds.temperature.resample(time="4D").sum().isel(site=0).values.tolist())
    print("rolling", ds.temperature.rolling(time=3).mean().isel(site=1).values[2:5].tolist())


def zarr_and_dask(ds: xr.Dataset, root: Path) -> None:
    print("engines", sorted(xr.backends.list_engines()))
    ds.to_zarr(root / "t.zarr", mode="w", consolidated=False)
    chunked = xr.open_zarr(root / "t.zarr", consolidated=False).chunk({"time": 4})
    print("chunks", chunked.temperature.chunks)
    print("dask mean", float(chunked.temperature.mean().compute()))


def distributed_part() -> None:
    from dask.distributed import Client, LocalCluster

    # The scheduler's HTTP server binds port 8787 even without the dashboard, and warns with
    # another port when that one is taken: port 0 never is.
    with LocalCluster(
        n_workers=2, threads_per_worker=1, processes=True, dashboard_address=None,
        scheduler_kwargs={"dashboard_address": ":0"}, silence_logs=logging.ERROR,
    ) as cluster, Client(cluster) as client:  # fmt: skip
        futures = client.map(lambda value: value * value, range(5))
        print("map", client.gather(futures))
        print("submit", client.submit(sum, [1, 2, 3]).result())
        array = xr.DataArray(np.arange(20.0), dims="x").chunk({"x": 5})
        print("workers", len(client.scheduler_info()["workers"]),
              "sum", float(array.sum().compute()))


def main() -> None:
    logging.getLogger("distributed").setLevel(logging.ERROR)
    ds = dataset()
    labelled(ds)
    with tempfile.TemporaryDirectory() as directory:
        zarr_and_dask(ds, Path(directory))
    distributed_part()
