"""jax with flax and optax: jit, grad and vmap, then a small flax.nnx MLP trained with adam."""

import jax
import jax.numpy as jnp
import optax
from flax import nnx


def transforms() -> None:
    key = jax.random.key(0)
    x = jax.random.normal(key, (4,))
    print("devices", sorted({device.platform for device in jax.devices()}))
    print("jit", round(float(jax.jit(lambda v: jnp.sum(jnp.tanh(v) ** 2))(x)), 5))
    print("grad", [round(float(g), 5) for g in jax.grad(lambda v: jnp.sum(v**3))(x)])
    batch = jax.random.normal(jax.random.split(key)[1], (3, 4))
    print("vmap", [round(float(v), 5) for v in jax.vmap(jnp.dot, in_axes=(0, None))(batch, x)])


class Mlp(nnx.Module):
    def __init__(self, rngs: nnx.Rngs) -> None:
        self.hidden = nnx.Linear(2, 8, rngs=rngs)
        self.out = nnx.Linear(8, 1, rngs=rngs)

    def __call__(self, x: jax.Array) -> jax.Array:
        return self.out(nnx.relu(self.hidden(x)))


def loss_fn(model: Mlp, x: jax.Array, y: jax.Array) -> jax.Array:
    return jnp.mean((model(x) - y) ** 2)


@nnx.jit
def train_step(model: Mlp, optimizer: nnx.Optimizer, x: jax.Array, y: jax.Array) -> jax.Array:
    loss, grads = nnx.value_and_grad(loss_fn)(model, x, y)
    optimizer.update(model, grads)
    return loss


def train() -> None:
    x = jax.random.normal(jax.random.key(1), (32, 2))
    y = (x[:, :1] * 2.0 - x[:, 1:]) + 0.5
    model = Mlp(nnx.Rngs(0))
    optimizer = nnx.Optimizer(model, optax.adam(0.05), wrt=nnx.Param)
    losses = [round(float(train_step(model, optimizer, x, y)), 3) for _ in range(5)]
    print("losses", losses)
    print("prediction", round(float(model(jnp.array([[1.0, 1.0]]))[0, 0]), 3))


def main() -> None:
    transforms()
    train()
