import csv
import hashlib
import json
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"
OUT = ROOT / "dictionaries"
AGENT = "LetopisiNature/1.0 (+https://github.com/leksika1/letopisi)"


class JsonClient:
    def __init__(self, cache: Path, interval: float) -> None:
        self.cache = cache
        self.interval = interval
        self.last = 0.0

    def get(self, url: str, **params: str | int) -> Any:
        if params:
            url += "?" + urlencode(sorted(params.items()))
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        for attempt in range(3):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                with urlopen(
                    Request(url, headers={"User-Agent": AGENT}), timeout=40
                ) as res:
                    result = json.load(res)
                break
            except HTTPError as exc:
                if (exc.code != 429 and exc.code < 500) or attempt == 2:
                    raise
                wait = exc.headers.get("Retry-After", "")
                time.sleep(int(wait) if wait.isdigit() else 2 ** (attempt + 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as tmp:
            json.dump(result, tmp, ensure_ascii=False)
        Path(tmp.name).replace(path)
        return result


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    Path(tmp.name).replace(path)
