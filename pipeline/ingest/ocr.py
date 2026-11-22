import base64
import hashlib
import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx

from .common import Box

URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"


def recognize(png: bytes, cache: Path) -> dict[str, Any] | None:
    target = cache / (hashlib.sha256(png).hexdigest() + ".json")
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    key, folder = (
        os.getenv("YC_API_KEY", "").strip(),
        os.getenv("YC_FOLDER_ID", "").strip(),
    )
    if not key or key == "сюда_ключ":
        return None
    if not folder:
        raise ValueError("YC_FOLDER_ID обязателен для OCR")
    body = {
        "mimeType": "PNG",
        "languageCodes": ["ru", "en"],
        "model": "page",
        "content": base64.b64encode(png).decode("ascii"),
    }
    for attempt in range(4):
        response = httpx.post(
            URL,
            json=body,
            headers={"Authorization": f"Api-Key {key}", "x-folder-id": folder},
            timeout=60,
        )
        if response.status_code != 429 and response.status_code < 500:
            break
        if attempt < 3:
            delay = response.headers.get("Retry-After", "")
            time.sleep(float(delay) if delay.isdigit() else 2**attempt)
    response.raise_for_status()
    data = response.json()
    if "textAnnotation" not in data.get("result", data):
        raise ValueError("Ответ OCR не содержит textAnnotation")
    cache.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=cache, delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False)
    Path(tmp.name).replace(target)
    return data


def lines(
    data: dict[str, Any], width: int, height: int
) -> list[tuple[str, Box, float | None]]:
    annotation = data.get("result", data)["textAnnotation"]
    width = int(annotation.get("width") or width)
    height = int(annotation.get("height") or height)
    result = []
    for block in annotation.get("blocks", []):
        for line in block.get("lines", []):
            points = line["boundingBox"]["vertices"]
            xs = [min(1.0, max(0.0, int(p.get("x", 0)) / width)) for p in points]
            ys = [min(1.0, max(0.0, int(p.get("y", 0)) / height)) for p in points]
            scores = [
                float(w["confidence"])
                for w in line.get("words", [])
                if "confidence" in w
            ]
            confidence = (
                float(line["confidence"])
                if "confidence" in line
                else (sum(scores) / len(scores) if scores else None)
            )
            result.append(
                (line["text"], (min(xs), min(ys), max(xs), max(ys)), confidence)
            )
    return result
