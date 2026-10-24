"""xgboost and lightgbm: models trained, saved and loaded; both load a shared library by path."""

import tempfile
from pathlib import Path

import lightgbm as lgb
import numpy as np
import xgboost as xgb
from sklearn.datasets import make_classification


def main() -> None:
    x, y = make_classification(n_samples=300, n_features=8, n_informative=4, random_state=0)
    print("data", x.shape, int(y.sum()))

    with tempfile.TemporaryDirectory() as directory:
        xgb_model = xgb.XGBClassifier(n_estimators=10, max_depth=3, n_jobs=1, random_state=0)
        xgb_model.fit(x, y)
        xgb_path = Path(directory) / "model.json"
        xgb_model.save_model(xgb_path)
        xgb_loaded = xgb.XGBClassifier()
        xgb_loaded.load_model(xgb_path)

        lgb_model = lgb.LGBMClassifier(n_estimators=10, n_jobs=1, random_state=0, verbose=-1)
        lgb_model.fit(x, y)
        lgb_path = Path(directory) / "model.txt"
        lgb_model.booster_.save_model(lgb_path)
        lgb_loaded = lgb.Booster(model_file=str(lgb_path))

        print("xgboost accuracy", round(float(xgb_model.score(x, y)), 4))
        print("xgboost predict",
              [round(float(p), 4) for p in xgb_loaded.predict_proba(x[:3])[:, 1]])
        print("lightgbm accuracy", round(float(lgb_model.score(x, y)), 4))
        print("lightgbm predict", np.round(lgb_loaded.predict(x[:3]), 4).tolist())
        print("saved", sorted(path.name for path in Path(directory).iterdir()))
