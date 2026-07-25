"""Byte and perceptual fingerprints for media captured by Recall."""
from __future__ import annotations
import hashlib
from io import BytesIO

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def image_dhash(data: bytes) -> str | None:
    """64-bit difference hash; None for unsupported/corrupt/non-image bytes."""
    try:
        from PIL import Image
        image = Image.open(BytesIO(data)).convert("L").resize((9, 8))
        values = list(image.getdata())
        bits = 0
        for row in range(8):
            for column in range(8):
                bits = (bits << 1) | int(values[row * 9 + column] > values[row * 9 + column + 1])
        return f"{bits:016x}"
    except Exception:
        return None

def hamming(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try: return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError: return None
