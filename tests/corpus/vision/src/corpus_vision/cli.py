"""opencv, scikit-image and imageio-ffmpeg on synthetic images and a synthetic video."""

import tempfile
from pathlib import Path

import cv2
import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
from skimage import data, filters, measure, transform


def opencv_part() -> None:
    image = np.zeros((64, 64), dtype=np.uint8)
    cv2.rectangle(image, (10, 10), (30, 40), 255, -1)
    cv2.circle(image, (48, 44), 8, 200, -1)
    blurred = cv2.GaussianBlur(image, (5, 5), 1.5)
    edges = cv2.Canny(blurred, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print("opencv", cv2.__version__.split(".")[0], "blur", int(blurred.sum()))
    print("canny", int(np.count_nonzero(edges)), "contours", len(contours))
    ok, encoded = cv2.imencode(".png", image)
    decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    print("png", ok, decoded.shape, bool((decoded == image).all()))


def skimage_part() -> None:
    camera = data.camera()
    edges = filters.sobel(camera)
    labels = measure.label(camera > 128)
    small = transform.resize(camera, (64, 64), anti_aliasing=True)
    print("camera", camera.shape, camera.dtype, int(camera.sum()))
    print("sobel", edges.shape, round(float(edges.sum()), 3))
    print("label", int(labels.max()))
    print("resize", small.shape, round(float(small.sum()), 3))


def video_part(directory: Path) -> None:
    path = directory / "clip.mp4"
    frames = [np.full((32, 48, 3), 25 * i, dtype=np.uint8) for i in range(10)]
    writer = imageio_ffmpeg.write_frames(str(path), (48, 32), fps=10, codec="libx264",
                                         macro_block_size=None)  # fmt: skip
    writer.send(None)
    for frame in frames:
        writer.send(frame.tobytes())
    writer.close()
    reader = imageio_ffmpeg.read_frames(str(path))
    meta = next(reader)
    count = sum(1 for _ in reader)
    print("ffmpeg", meta["size"], meta["codec"], "frames", count)
    read_back = imageio.mimread(str(path), format="ffmpeg", memtest=False)
    print("imageio", len(read_back), read_back[0].shape)


def main() -> None:
    opencv_part()
    skimage_part()
    with tempfile.TemporaryDirectory() as directory:
        video_part(Path(directory))
