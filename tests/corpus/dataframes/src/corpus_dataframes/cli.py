"""polars, pyarrow and duckdb exchanging Arrow data: Rust and C++ extension modules."""

import os

os.environ["POLARS_MAX_THREADS"] = "1"  # before polars is imported: a fixed thread pool

import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq


def polars_part() -> pl.DataFrame:
    orders = pl.LazyFrame(
        {"customer": [1, 2, 1, 3, 2, 1], "amount": [10.5, 20.0, 7.25, 3.0, 8.0, 1.25]}
    )
    customers = pl.LazyFrame({"customer": [1, 2, 3], "name": ["ada", "grace", "alan"]})
    result = (
        orders.group_by("customer")
        .agg(pl.col("amount").sum().alias("total"), pl.len().alias("orders"))
        .join(customers, on="customer")
        .sort("customer")
        .collect()
    )
    print("polars", result.rows())
    return result


def arrow_part(root: Path) -> pa.Table:
    table = pa.table(
        {"id": [1, 2, 3, 4], "kind": ["a", "b", "a", "b"], "value": [1.5, 2.5, 3.5, 4.5]}
    )
    print("arrow schema", table.schema.names, table.num_rows)
    print("compute sum", pc.sum(table["value"]).as_py(), "max", pc.max(table["id"]).as_py())
    print("compute upper", pc.utf8_upper(table["kind"]).to_pylist())
    pq.write_table(table, root / "one.parquet")
    print("parquet", pq.read_table(root / "one.parquet").equals(table))
    folder = root / "dataset"
    folder.mkdir()
    for part in range(2):
        pq.write_table(table.slice(2 * part, 2), folder / f"part-{part}.parquet")
    dataset = ds.dataset(folder, format="parquet")
    big = dataset.to_table(filter=ds.field("value") > 2.0).sort_by("id")
    print("dataset", dataset.count_rows(), big["id"].to_pylist())
    return table


def duckdb_part(table: pa.Table) -> None:
    frame = pd.DataFrame({"kind": ["a", "b"], "label": ["first", "second"]})
    with duckdb.connect() as connection:
        connection.register("t", table)
        connection.register("labels", frame)
        rows = connection.sql(
            "select l.label, sum(t.value) as total from t join labels l using (kind) "
            "group by l.label order by 1"
        ).fetchall()
        print("duckdb", rows)
        print("duckdb arrow",
              connection.sql("select count(*) as n from t").arrow().read_all().to_pylist())


def conversions(result: pl.DataFrame) -> None:
    arrow = result.to_arrow()
    frame = arrow.to_pandas()
    print("polars to arrow to pandas", arrow.schema.names, frame["total"].round(2).tolist())
    print("pandas to polars", pl.from_pandas(frame).sort("customer")["name"].to_list())


def main() -> None:
    result = polars_part()
    with tempfile.TemporaryDirectory() as directory:
        table = arrow_part(Path(directory))
    duckdb_part(table)
    conversions(result)
