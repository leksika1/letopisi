import csv
import json
from pathlib import Path
from unittest.mock import Mock

from dictionaries.scripts.gbif_enrich import enrich
from dictionaries.scripts.geocode_places import geocode

DATA = json.loads(
    Path(__file__).with_name("responses.json").read_text(encoding="utf-8")
)


def rows(root: Path, name: str) -> list[dict]:
    with (root / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_synonyms_and_review(tmp_path: Path) -> None:
    client = Mock()
    client.get.side_effect = [
        DATA["match"],
        DATA["synonyms"],
        DATA["no_match"],
        {**DATA["match"], "rank": "VARIETY"},
    ]
    enrich(
        [
            {"name_lat": "Salix caprea", "name_ru": "Ива"},
            {"name_lat": "Unknown"},
            {"name_lat": "Unlisted variety"},
        ],
        client,
        tmp_path,
    )
    assert rows(tmp_path, "taxa.csv")[0]["group_ru"] == "сосудистые растения"
    assert len(rows(tmp_path, "taxon_names.csv")) == 3
    review = rows(tmp_path, "review_taxa.csv")
    assert (
        review[0]["matchType"] == "NONE" and review[1]["name_lat"] == "Unlisted variety"
    )


def test_places_bounds_and_review(tmp_path: Path) -> None:
    client = Mock()
    client.get.side_effect = [DATA["places"], []]
    geocode(
        [{"name": "Паз", "category": "река"}, {"name": "Нет совпадения"}],
        client,
        tmp_path,
    )
    assert rows(tmp_path, "places.csv")[0]["lat"] == "69.2"
    assert rows(tmp_path, "places.csv")[0]["coords_source"] == "gazetteer"
    assert rows(tmp_path, "review_places.csv")[0]["lat"] == ""
    assert client.get.call_args.kwargs["bounded"] == 1


def test_enrichment_preserves_curated_ids(tmp_path: Path) -> None:
    client = Mock()
    client.get.side_effect = [DATA["match"], DATA["synonyms"]]
    old = [
        {"id": 74, "taxon_id": 3, "name": "Ива", "lang": "ru", "kind": "accepted"},
        {
            "id": 75,
            "taxon_id": 8,
            "name": "Другое название",
            "lang": "ru",
            "kind": "variant",
        },
    ]
    enrich(
        [{"id": 3, "name_lat": "Salix caprea", "name_ru": "Ива"}], client, tmp_path, old
    )
    names = rows(tmp_path, "taxon_names.csv")
    assert next(r for r in names if r["name"] == "Ива")["id"] == "74"
    assert all(int(r["id"]) > 75 for r in names if r["name"] != "Ива")
