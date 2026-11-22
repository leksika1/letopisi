import csv
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode

import pytest
from jsonschema import Draft202012Validator

from pipeline.extract.extractor import DocContext, build
from pipeline.extract.tables.common import Draft
from pipeline.normalize import dicts, places, taxa

ROOT = Path(__file__).resolve().parents[2]


def cache_response(path: Path, url: str, params: dict, response: object) -> None:
    url += "?" + urlencode(sorted(params.items()))
    name = hashlib.sha256(url.encode()).hexdigest() + ".json"
    path.mkdir(exist_ok=True)
    (path / name).write_text(json.dumps(response), encoding="utf-8")


def run_cli(module: str, source: Path, cache: Path, output: Path) -> None:
    # Misses must fail locally. Neither a provider account nor Internet access is needed.
    env = {**os.environ, "https_proxy": "http://127.0.0.1:1", "no_proxy": ""}
    subprocess.run(
        [
            sys.executable,
            "-m",
            module,
            "--input",
            source,
            "--cache",
            cache,
            "--output",
            output,
        ],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=15,
    )


@pytest.fixture
def generated_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    output = tmp_path / "dictionaries"
    monkeypatch.setattr(dicts, "DICT_DIR", output)
    indexes = (dicts.load, places.default_index, taxa.default_index)
    for index in indexes:
        index.cache_clear()
    try:
        yield output
    finally:
        for index in indexes:
            index.cache_clear()


def test_enriched_taxon_and_river_normalize_an_observation(
    tmp_path: Path,
    generated_catalog: Path,
) -> None:
    saved = json.loads(
        (ROOT / "dictionaries/scripts/tests/responses.json").read_text(encoding="utf-8")
    )
    gbif = "https://api.gbif.org/v1/species"
    latin, synonym, key = "Salix alba", "Salix caerulea", 910001
    species = tmp_path / "species.csv"
    species.write_text(f"name_ru,name_lat\nИва белая,{latin}\n", encoding="utf-8")
    cache = tmp_path / "gbif-cache"
    cache_response(
        cache,
        f"{gbif}/match",
        {"name": latin},
        {**saved["match"], "canonicalName": latin, "usageKey": key},
    )
    cache_response(
        cache,
        f"{gbif}/{key}/synonyms",
        {"limit": 100, "offset": 0},
        {"endOfRecords": True, "results": [{"canonicalName": synonym}]},
    )
    run_cli("dictionaries.scripts.gbif_enrich", species, cache, generated_catalog)

    river = "Сосновка"
    source = tmp_path / "rivers.json"
    source.write_text(
        json.dumps([{"id": 701, "name": river, "category": "река"}]), encoding="utf-8"
    )
    cache = tmp_path / "nominatim-cache"
    cache_response(
        cache,
        "https://nominatim.openstreetmap.org/search",
        {
            "q": river,
            "format": "jsonv2",
            "limit": 5,
            "bounded": 1,
            "viewbox": "28.6,69.5,30.2,68.9",
            "accept-language": "ru",
        },
        saved["places"],
    )
    run_cli("dictionaries.scripts.geocode_places", source, cache, generated_catalog)

    quote = f"20 июня 2020 г. у реки {river} отмечена {synonym}."
    draft = Draft(
        {
            "record_type": "observation",
            "object_type": "taxon",
            "object_raw": synonym,
            "date_raw": "20 июня 2020 г.",
            "toponym_raw": river,
            "event_raw": "отмечена",
        },
        quote,
    )
    chunk = {
        "id": 560,
        "text": quote,
        "locator": {"pdf_page": 42, "half": None, "printed_page": "41"},
    }
    obs = build(
        draft, chunk, DocContext(3, 2020, "Летопись 2020", None, {}), obs_id=850
    )
    schema = json.loads(
        (ROOT / "contracts/observation.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    ).validate(obs)
    assert obs["taxon_id"] == key and obs["place_id"] == 701
    assert obs["quote_span"] == [0, len(quote)] and obs["date_from"] == "2020-06-20"
    catalog = dicts.load()
    taxon = catalog.taxon(obs["taxon_id"])
    assert taxon is not None and taxon["name_lat"] == latin
    location = catalog.place(obs["place_id"])
    assert location is not None
    assert (float(location["lat"]), float(location["lon"])) == (69.2, 29.4)
    assert location["coords_source"] == "gazetteer" and location["source"].endswith(
        "/way/2"
    )
    for name in ("review_taxa.csv", "review_places.csv"):
        with (generated_catalog / name).open(encoding="utf-8", newline="") as stream:
            assert list(csv.DictReader(stream)) == []
