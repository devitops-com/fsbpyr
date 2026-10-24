"""Builds faiss indexes on seeded data, searches them and writes one to disk."""

import tempfile
from pathlib import Path

import faiss
import numpy as np

DIMENSION = 16


def show(label: str, distances: np.ndarray, ids: np.ndarray) -> None:
    print(label, ids.tolist(), np.round(distances.astype(float), 3).tolist())


def main() -> None:
    faiss.omp_set_num_threads(1)
    rng = np.random.default_rng(0)
    vectors = rng.standard_normal((500, DIMENSION)).astype(np.float32)
    queries = vectors[:2] + 0.01

    flat = faiss.IndexFlatL2(DIMENSION)
    flat.add(vectors)
    print("flat total", flat.ntotal)
    show("flat search", *flat.search(queries, 3))

    quantizer = faiss.IndexFlatL2(DIMENSION)
    ivf = faiss.IndexIVFFlat(quantizer, DIMENSION, 8)
    ivf.train(vectors)
    ivf.add(vectors)
    ivf.nprobe = 3
    print("ivf trained", ivf.is_trained, "lists", ivf.nlist)
    show("ivf search", *ivf.search(queries, 3))

    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "flat.index")
        faiss.write_index(flat, path)
        loaded = faiss.read_index(path)
    show("reloaded search", *loaded.search(queries, 3))
