"""Decode QR codes found in images linked from a page.

Uses OpenCV's built-in QRCodeDetector so there's no system libzbar
dependency to install on the demo machine.
"""
from __future__ import annotations

import cv2
import numpy as np
import httpx

_detector = cv2.QRCodeDetector()

MAX_IMAGE_BYTES = 5_000_000


async def decode_qr_from_url(client: httpx.AsyncClient, image_url: str) -> str | None:
    try:
        resp = await client.get(image_url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > MAX_IMAGE_BYTES:
            return None
        buf = np.frombuffer(resp.content, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            return None
        data, _points, _ = _detector.detectAndDecode(img)
        return data or None
    except Exception:
        return None
