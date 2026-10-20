"""Draws an image with Pillow, shrinks it to a thumbnail and encodes it in three formats."""

import hashlib
import io

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 380, 280), outline="navy", width=5)
    draw.text((40, 40), "fsbpyr", fill="black", font=ImageFont.load_default(size=32))
    image.thumbnail((100, 100))
    print("thumbnail", image.size, image.mode)
    for name in ("PNG", "JPEG", "WEBP"):
        buffer = io.BytesIO()
        image.save(buffer, format=name)
        again = Image.open(io.BytesIO(buffer.getvalue()))
        print(name, again.format, again.size, hashlib.sha256(again.tobytes()).hexdigest()[:16])
