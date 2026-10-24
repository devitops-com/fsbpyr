"""scikit-learn: bundled datasets, a Pipeline, and joblib's worker processes."""

import numpy as np
from joblib import Parallel, delayed
from sklearn.datasets import load_digits, load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_info


def square(value: int) -> int:
    return value * value


def main() -> None:
    iris = load_iris()
    digits = load_digits()
    print("iris", iris.data.shape, [str(name) for name in iris.target_names])
    print("digits", digits.data.shape, digits.images.shape)

    pipeline = Pipeline(
        [("scale", StandardScaler()), ("model", LogisticRegression(max_iter=500))]
    )
    scores = cross_val_score(pipeline, iris.data, iris.target, cv=5, n_jobs=2)
    print("cross_val_score", np.round(scores, 4).tolist())

    forest = RandomForestClassifier(n_estimators=20, n_jobs=2, random_state=0)
    forest.fit(digits.data[:1000], digits.target[:1000])
    print("forest score", round(float(forest.score(digits.data[1000:], digits.target[1000:])), 4))

    print("parallel", Parallel(n_jobs=2)(delayed(square)(value) for value in range(8)))
    print("threadpool", sorted({pool["user_api"] for pool in threadpool_info()}))
