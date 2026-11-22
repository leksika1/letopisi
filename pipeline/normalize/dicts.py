"""Загрузка справочников.

Стартовый источник — ``contracts/fixtures/*.json``. Если появились ``dictionaries/*.csv``
(задача Б-4: taxa.csv, taxon_names.csv, places.csv), они дополняют фикстуры: строки с тем же
id заменяют фикстурные, новые добавляются.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"
DICT_DIR = ROOT / "dictionaries"


def _load_json(name: str) -> list[dict]:
    p = FIXTURES / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _load_csv(name: str) -> list[dict]:
    p = DICT_DIR / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        r = {k: (v if v != "" else None) for k, v in r.items()}
        for k in ("id", "taxon_id", "place_id", "gbif_key"):
            if r.get(k) is not None:
                try:
                    r[k] = int(r[k])
                except ValueError:
                    pass
        out.append(r)
    return out


def _merge(base: list[dict], extra: list[dict]) -> list[dict]:
    if not extra:
        return base
    by_id = {r["id"]: r for r in base if "id" in r}
    for r in extra:
        if r.get("id") is None:
            continue
        by_id[r["id"]] = {**by_id.get(r["id"], {}), **r}
    return sorted(by_id.values(), key=lambda r: r["id"])


@dataclass
class Dictionaries:
    documents: list[dict] = field(default_factory=list)
    taxa: list[dict] = field(default_factory=list)
    taxon_names: list[dict] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)
    place_names: list[dict] = field(default_factory=list)
    persons: list[dict] = field(default_factory=list)
    phenophases: list[dict] = field(default_factory=list)

    def taxon(self, tid: int | None) -> dict | None:
        return self._taxa_by_id.get(tid) if tid else None

    def place(self, pid: int | None) -> dict | None:
        return self._places_by_id.get(pid) if pid else None

    def phenophase(self, pid: int | None) -> dict | None:
        return self._ph_by_id.get(pid) if pid else None

    def document(self, did: int) -> dict | None:
        return next((d for d in self.documents if d["id"] == did), None)

    def __post_init__(self):
        self._taxa_by_id = {t["id"]: t for t in self.taxa}
        self._places_by_id = {p["id"]: p for p in self.places}
        self._ph_by_id = {p["id"]: p for p in self.phenophases}


@lru_cache(maxsize=1)
def load() -> Dictionaries:
    return Dictionaries(
        documents=_load_json("documents.json"),
        taxa=_merge(_load_json("taxa.json"), _load_csv("taxa.csv")),
        taxon_names=_merge(_load_json("taxon_names.json"), _load_csv("taxon_names.csv")),
        places=_merge(_load_json("places.json"), _load_csv("places.csv")),
        place_names=_merge(_load_json("place_names.json"), _load_csv("place_names.csv")),
        persons=_load_json("persons.json"),
        phenophases=_load_json("phenophases.json"),
    )
