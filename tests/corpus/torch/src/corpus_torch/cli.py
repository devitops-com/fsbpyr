"""Trains, scripts, compiles and loads with torch (CPU)."""

import tempfile
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset


class Squares(Dataset):
    """A map-style dataset: item i is the vector [i, i*i]."""

    def __len__(self) -> int:
        return 12

    def __getitem__(self, index: int) -> torch.Tensor:
        return torch.tensor([float(index), float(index * index)])


class Gate(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.linear = nn.Linear(3, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.sum() > 0:
            return torch.relu(self.linear(x))
        return -self.linear(x)


def scaled_sum(x: torch.Tensor, factor: float) -> torch.Tensor:
    total = torch.zeros(1)
    for value in x:
        total = total + value * factor
    return total


def build_mlp() -> nn.Sequential:
    return nn.Sequential(nn.Linear(4, 8), nn.Tanh(), nn.Linear(8, 1))


def train() -> None:
    generator = torch.Generator().manual_seed(0)
    inputs = torch.randn(64, 4, generator=generator)
    targets = inputs @ torch.tensor([[1.0], [-2.0], [0.5], [3.0]]) + 0.1
    model = build_mlp()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.05)
    for epoch in range(4):
        optimizer.zero_grad()
        loss = nn.functional.mse_loss(model(inputs), targets)
        loss.backward()
        optimizer.step()
        print(f"train epoch {epoch} loss {loss.item():.4f}")


def scripted() -> None:
    function = torch.jit.script(scaled_sum)
    print("script function", function(torch.tensor([1.0, 2.0, 3.0]), 0.5).tolist())
    module = torch.jit.script(Gate())
    x = torch.ones(1, 3)
    print("script module", [round(v, 4) for v in module(x).flatten().tolist()])
    print("script graph has if:", "prim::If" in str(module.graph))


def compiled() -> None:
    def fn(x: torch.Tensor) -> torch.Tensor:
        return torch.sin(x) + x.pow(2)

    result = torch.compile(fn, backend="eager")(torch.tensor([0.0, 1.0, 2.0]))
    print("compile eager", [round(v, 4) for v in result.tolist()])


def loader() -> None:
    batches = DataLoader(Squares(), batch_size=4, num_workers=2)
    for number, batch in enumerate(batches):
        print(f"loader batch {number} sum {batch.sum().item():.0f}")


def checkpoint() -> None:
    torch.manual_seed(0)
    model = build_mlp()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "model.pt"
        torch.save(model.state_dict(), path)
        restored = build_mlp()
        restored.load_state_dict(torch.load(path, weights_only=True))
    same = all(
        torch.equal(a, b) for a, b in zip(model.parameters(), restored.parameters(), strict=False)
    )
    print("checkpoint round trip", same)


def main() -> None:
    torch.manual_seed(0)
    torch.set_num_threads(1)
    train()
    scripted()
    compiled()
    loader()
    checkpoint()
