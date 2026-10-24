"""Runs torchvision's transforms, operators, image codecs and a model."""

import torch
from torch import nn
from torchvision import io, ops, transforms
from torchvision.models import resnet18
from torchvision.transforms import v2


def summary(tensor: torch.Tensor) -> str:
    return f"shape {tuple(tensor.shape)} sum {tensor.double().sum().item():.4f}"


def pipeline(image: torch.Tensor) -> None:
    transform = v2.Compose([
        v2.Resize((24, 24), antialias=True),
        v2.CenterCrop(16),
        v2.ToDtype(torch.float32, scale=True),
        v2.Normalize(mean=[0.5, 0.5, 0.5], std=[0.25, 0.25, 0.25]),
    ])  # fmt: skip
    print("transforms v2", summary(transform(image)))


def operators() -> None:
    boxes = torch.tensor([[0.0, 0, 10, 10], [1, 1, 11, 11], [20, 20, 30, 30]])
    scores = torch.tensor([0.9, 0.8, 0.7])
    print("nms keep", ops.nms(boxes, scores, 0.5).tolist())
    features = torch.arange(2 * 1 * 8 * 8, dtype=torch.float32).reshape(2, 1, 8, 8)
    rois = torch.tensor([[0, 0.0, 0.0, 4.0, 4.0], [1, 2.0, 2.0, 6.0, 6.0]])
    print("roi_align", summary(ops.roi_align(features, rois, output_size=2)))
    print("registered ops", torch.ops.torchvision.nms is not None)


def image_codec(image: torch.Tensor) -> None:
    encoded = io.encode_png(image)
    decoded = io.decode_png(encoded)
    print("png round trip", torch.equal(image, decoded), encoded.numel() > 0)


def model_forward() -> None:
    model = resnet18(weights=None).eval()
    with torch.no_grad():
        logits = model(torch.linspace(-1, 1, 3 * 32 * 32).reshape(1, 3, 32, 32))
    print("resnet18 logits", summary(logits), "argmax", logits.argmax().item())


def scripted_transforms() -> None:
    scripted = torch.jit.script(nn.Sequential(
        transforms.CenterCrop(8), transforms.ConvertImageDtype(torch.float32),
    ))  # fmt: skip
    print("script transforms", summary(scripted(torch.full((3, 12, 12), 255, dtype=torch.uint8))))


def main() -> None:
    torch.manual_seed(0)
    torch.set_num_threads(1)
    image = torch.randint(0, 256, (3, 32, 40), dtype=torch.uint8)
    pipeline(image)
    operators()
    image_codec(image)
    model_forward()
    scripted_transforms()
