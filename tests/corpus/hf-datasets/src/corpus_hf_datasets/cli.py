"""Builds, maps (in worker processes), filters, splits and stores a Hugging Face dataset."""

import os
import tempfile


def add_square(row: dict) -> dict:
    return {"square": row["n"] * row["n"], "label": "even" if row["n"] % 2 == 0 else "odd"}


def run(home: str) -> None:
    # Set before datasets is imported: its cache paths are read at import time.
    os.environ.update(HF_HOME=home, HF_DATASETS_CACHE=os.path.join(home, "datasets"),
                      HF_HUB_OFFLINE="1")  # fmt: skip
    import datasets
    from datasets import Dataset, load_from_disk

    datasets.disable_progress_bars()
    data = Dataset.from_dict({"n": list(range(20)), "name": [f"item{n}" for n in range(20)]})
    print("columns", data.column_names, "rows", len(data))
    mapped = data.map(add_square, num_proc=2)
    print("mapped", mapped[3])
    odd = mapped.filter(lambda row: row["label"] == "odd")
    print("odd rows", len(odd), list(odd["n"])[:5])
    split = mapped.train_test_split(test_size=0.25, seed=0)
    print("split", len(split["train"]), len(split["test"]), list(split["test"]["n"]))
    path = os.path.join(home, "saved")
    split.save_to_disk(path)
    loaded = load_from_disk(path)
    print("reloaded", list(loaded["test"]["square"]))
    print(loaded["train"].to_pandas().sort_values("n").head(3).to_string())


def main() -> None:
    with tempfile.TemporaryDirectory() as home:
        run(home)
