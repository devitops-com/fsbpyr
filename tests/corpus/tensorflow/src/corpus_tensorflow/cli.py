"""TensorFlow: tf.function with a GradientTape, a tf.data pipeline, a Keras model, a SavedModel."""

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"  # before the import: TensorFlow reads it then


@contextlib.contextmanager
def quiet_stderr() -> Iterator[None]:
    """TensorFlow's C++ code logs to file descriptor 2 before absl is initialised, whatever
    TF_CPP_MIN_LOG_LEVEL says, with a timestamp and a process id in every line."""
    saved = os.dup(2)
    with open(os.devnull, "w") as devnull:
        os.dup2(devnull.fileno(), 2)
        try:
            yield
        finally:
            os.dup2(saved, 2)
            os.close(saved)


with quiet_stderr():
    import tensorflow as tf


def gradient() -> None:
    w = tf.Variable([2.0, -1.0])

    @tf.function
    def step(x: tf.Tensor) -> tf.Tensor:
        with tf.GradientTape() as tape:
            loss = tf.reduce_sum((w * x) ** 2)
        return tape.gradient(loss, w)

    print("gradient", [round(float(g), 4) for g in step(tf.constant([3.0, 4.0]))])


def pipeline() -> None:
    def square(v: tf.Tensor) -> tf.Tensor:
        return v * v

    def is_even(v: tf.Tensor) -> tf.Tensor:
        return v % 2 == 0

    dataset = tf.data.Dataset.range(10).map(square).filter(is_even).batch(3)
    print("dataset", [batch.numpy().tolist() for batch in dataset])


def model(directory: Path) -> None:
    x = tf.random.normal((64, 4))
    y = tf.reduce_sum(x * tf.constant([1.0, -2.0, 0.5, 3.0]), axis=1, keepdims=True)
    net = tf.keras.Sequential(
        [tf.keras.Input(shape=(4,)), tf.keras.layers.Dense(8, activation="relu"),
         tf.keras.layers.Dense(1)]
    )
    net.compile(optimizer=tf.keras.optimizers.Adam(0.05), loss="mse")
    history = net.fit(x, y, epochs=3, batch_size=16, shuffle=False, verbose=0)
    print("fit", [round(float(loss), 3) for loss in history.history["loss"]])

    module = tf.Module()
    module.net = net
    module.call = tf.function(net, input_signature=[tf.TensorSpec([None, 4])])
    tf.saved_model.save(module, str(directory / "saved"))
    loaded = tf.saved_model.load(str(directory / "saved"))
    out = loaded.call(x[:3]).numpy()
    print("saved model", [round(float(v), 3) for v in out[:, 0]])
    print("same", bool(tf.reduce_all(tf.abs(out - net(x[:3])) < 1e-5)))


def main() -> None:
    tf.keras.utils.set_random_seed(0)  # python, numpy and tf.random; keras' initialisers too
    tf.config.experimental.enable_op_determinism()
    print("tensorflow", tf.__version__.split(".")[0])
    gradient()
    pipeline()
    with tempfile.TemporaryDirectory() as directory:
        model(Path(directory))
