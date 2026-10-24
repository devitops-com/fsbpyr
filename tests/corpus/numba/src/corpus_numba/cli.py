"""numba: njit loops, vectorize, parallel prange, typed containers and an on-disk cache."""

import os

os.environ["NUMBA_NUM_THREADS"] = "2"  # before the import: numba reads it at import

import numba
import numpy as np
from numba import njit, prange, typed, types, vectorize


@njit
def triangular(n: int) -> int:
    total = 0
    for i in range(n):
        total += i
    return total


@vectorize(["float64(float64, float64)"])
def hypot(a, b):
    return (a * a + b * b) ** 0.5


@njit(parallel=True)
def parallel_sum(values):
    total = 0.0
    for i in prange(values.shape[0]):
        total += values[i] * 2.0
    return total


@njit(cache=True)
def cached_polynomial(x: float) -> float:
    return x**3 - 2.0 * x + 1.0


def containers() -> None:
    items = typed.List([3, 1, 2])
    items.append(5)
    counts = typed.Dict.empty(types.unicode_type, types.int64)
    for word in ["a", "b", "a", "c", "b", "a"]:
        counts[word] = counts.get(word, 0) + 1
    print("typed list", list(items))
    print("typed dict", sorted(counts.items()))


def main() -> None:
    print("njit", triangular(1000))
    print("vectorize", hypot(np.array([3.0, 5.0]), np.array([4.0, 12.0])).tolist())
    print("prange", parallel_sum(np.arange(1000.0)))
    print("threading layer", numba.threading_layer() in {"workqueue", "omp", "tbb"})
    containers()
    print("cached", cached_polynomial(2.0), cached_polynomial(3.0))
