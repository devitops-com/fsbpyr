"""lmfit and emcee: a least-squares model fit and an ensemble MCMC, both seeded."""

import numpy as np
from emcee import EnsembleSampler
from lmfit.models import GaussianModel


def fit_gaussian() -> None:
    rng = np.random.default_rng(42)
    x = np.linspace(-5, 5, 101)
    y = 3.0 * np.exp(-((x - 0.5) ** 2) / (2 * 1.2**2)) + rng.normal(0, 0.05, x.size)
    model = GaussianModel()
    result = model.fit(y, model.make_params(amplitude=5, center=0, sigma=1), x=x)
    for name, parameter in result.params.items():
        print("fit", name, round(parameter.value, 3), round(parameter.stderr, 3))
    print("fit success", result.success, "points", result.ndata, "redchi", round(result.redchi, 4))


def log_probability(theta: np.ndarray) -> float:
    return float(-0.5 * np.sum((theta - np.array([1.0, -2.0])) ** 2 / np.array([1.0, 4.0])))


def sample() -> None:
    rng = np.random.default_rng(7)
    sampler = EnsembleSampler(16, 2, log_probability)
    sampler.random_state = np.random.RandomState(1).get_state()
    sampler.run_mcmc(rng.normal(size=(16, 2)), 600, progress=False)
    chain = sampler.get_chain(discard=200, flat=True)
    print("mcmc mean", np.round(chain.mean(axis=0), 1).tolist())
    print("mcmc std", np.round(chain.std(axis=0), 1).tolist())
    print("mcmc shape", chain.shape)


def main() -> None:
    fit_gaussian()
    sample()
