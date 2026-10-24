"""Trains a tiny classifier with Lightning, saves and reloads its checkpoint."""

import tempfile
from pathlib import Path

import lightning as L
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset
from torchmetrics.classification import Accuracy


class Classifier(L.LightningModule):
    def __init__(self, hidden: int = 8, lr: float = 0.1) -> None:
        super().__init__()
        self.save_hyperparameters()
        self.net = nn.Sequential(nn.Linear(2, hidden), nn.ReLU(), nn.Linear(hidden, 2))
        self.accuracy = Accuracy(task="multiclass", num_classes=2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    def training_step(self, batch, batch_index):
        x, y = batch
        return nn.functional.cross_entropy(self(x), y)

    def configure_optimizers(self):
        return torch.optim.SGD(self.parameters(), lr=self.hparams.lr)


def dataset() -> TensorDataset:
    generator = torch.Generator().manual_seed(0)
    x = torch.randn(64, 2, generator=generator)
    return TensorDataset(x, (x[:, 0] + x[:, 1] > 0).long())


def main() -> None:
    torch.set_num_threads(1)
    L.seed_everything(0, workers=True)
    data = dataset()
    with tempfile.TemporaryDirectory() as directory:
        trainer = L.Trainer(
            max_epochs=2, accelerator="cpu", devices=1, deterministic=True, logger=False,
            enable_progress_bar=False, enable_model_summary=False, default_root_dir=directory,
        )  # fmt: skip
        model = Classifier(hidden=8)
        trainer.fit(model, DataLoader(data, batch_size=16))
        print("trained epochs", trainer.current_epoch, "steps", trainer.global_step)
        path = Path(directory) / "model.ckpt"
        trainer.save_checkpoint(path)
        restored = Classifier.load_from_checkpoint(path)
    print("hyperparameters", dict(restored.hparams))
    x, y = data.tensors
    with torch.no_grad():
        for name, module in (("trained", model), ("restored", restored)):
            accuracy = module.accuracy(module(x).argmax(dim=1), y)
            print(f"{name} accuracy {accuracy.item():.4f}")
    print("same weights", all(torch.equal(a, b)
          for a, b in zip(model.parameters(), restored.parameters(), strict=False)))  # fmt: skip
