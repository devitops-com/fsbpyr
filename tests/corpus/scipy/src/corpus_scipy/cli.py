"""A little of scipy's compiled libraries: linalg, optimize, special, sparse, FFT."""

import numpy as np
from scipy import fft, linalg, optimize, sparse, special


def main() -> None:
    a = np.array([[3.0, 1.0], [1.0, 2.0]])
    print("solve", np.round(linalg.solve(a, np.array([9.0, 8.0])), 6).tolist())
    result = optimize.minimize_scalar(lambda x: (x - 2) ** 2 + 1)
    print("minimum", round(float(result.x), 4), round(float(result.fun), 4))
    print("gamma", round(float(special.gamma(5.5)), 6), round(float(special.erf(0.5)), 6))
    print("sparse", sparse.eye(3, format="csr").dot(np.arange(3.0)).tolist())
    print("fft", np.round(np.abs(fft.fft([1.0, 0.0, -1.0, 0.0])), 6).tolist())
