"""Runs an optuna study stored in SQLite, whose schema alembic migrates."""

import tempfile
from pathlib import Path

import optuna


def objective(trial: optuna.Trial) -> float:
    x = trial.suggest_float("x", -10, 10)
    y = trial.suggest_int("y", -5, 5)
    return (x - 2) ** 2 + (y + 1) ** 2


def main() -> None:
    optuna.logging.set_verbosity(optuna.logging.WARNING)  # the info logs carry timestamps
    with tempfile.TemporaryDirectory() as directory:
        storage = f"sqlite:///{Path(directory) / 'study.db'}"
        study = optuna.create_study(
            study_name="quadratic", storage=storage, sampler=optuna.samplers.TPESampler(seed=0),
        )  # fmt: skip
        study.optimize(objective, n_trials=20)
        loaded = optuna.load_study(study_name="quadratic", storage=storage)
        print("trials", len(loaded.trials))
        print("best value", round(loaded.best_value, 4))
        print("best params", {k: round(v, 4) for k, v in loaded.best_params.items()})
