"""keras 3 on the jax backend: a Sequential model fitted, evaluated, saved and loaded."""

import os
import tempfile
from pathlib import Path

import numpy as np


def run(directory: Path) -> None:
    import keras  # after the environment: keras reads these when it is imported

    keras.utils.set_random_seed(0)
    print("backend", keras.backend.backend())
    rng = np.random.default_rng(0)
    x = rng.normal(size=(64, 4)).astype("float32")
    y = (x @ np.array([[1.0], [-2.0], [0.5], [3.0]], dtype="float32")).astype("float32")
    model = keras.Sequential(
        [keras.Input(shape=(4,)), keras.layers.Dense(8, activation="relu"), keras.layers.Dense(1)]
    )
    model.compile(optimizer=keras.optimizers.Adam(0.05), loss="mse")
    history = model.fit(x, y, epochs=3, batch_size=16, shuffle=False, verbose=0)
    print("fit", [round(float(loss), 3) for loss in history.history["loss"]])
    print("evaluate", round(float(model.evaluate(x, y, verbose=0)), 3))
    path = directory / "m.keras"
    model.save(path)
    loaded = keras.saving.load_model(path)
    print("saved", path.name, path.stat().st_size > 0)
    predicted = loaded.predict(x[:3], verbose=0)
    print("predict", [round(float(v), 3) for v in predicted[:, 0]])
    print("same", bool(np.allclose(model.predict(x[:3], verbose=0), predicted, atol=1e-5)))


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        os.environ["KERAS_BACKEND"] = "jax"
        os.environ["KERAS_HOME"] = directory
        run(Path(directory))
