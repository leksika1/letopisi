import csv
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

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


def gbif_client(species: dict[str, tuple[int, str]]) -> Mock:
    def response(url: str, **params: str | int) -> dict:
        if url.endswith("/match"):
            key, canonical = species[str(params["name"])]
            return {**DATA["match"], "usageKey": key, "canonicalName": canonical}
        assert url.endswith("/synonyms")
        return {"results": [], "endOfRecords": True}

    client = Mock()
    client.get.side_effect = response
    return client


def test_alias_and_canonical_share_fixture_id_in_both_orders(tmp_path: Path) -> None:
    canonical, alias = "Betula pubescens", "Betula pubescens Ehrh."
    source = [{"name_lat": alias}, {"name_lat": canonical}]
    known = [{"id": 4, "name_lat": canonical, "name_ru": "Береза пушистая"}]
    client = gbif_client(dict.fromkeys((alias, canonical), (9118014, canonical)))
    for directory, ordered in [
        (tmp_path / "a", source),
        (tmp_path / "b", source[::-1]),
    ]:
        enrich(ordered, client, directory, existing_taxa=known)
        taxa = rows(directory, "taxa.csv")
        assert [(r["id"], r["name_ru"]) for r in taxa] == [("4", "Береза пушистая")]
        assert {r["taxon_id"] for r in rows(directory, "taxon_names.csv")} == {"4"}


def test_new_ids_survive_reordering_subsets_and_additions(tmp_path: Path) -> None:
    a, b, c = "Salix alba", "Salix fragilis", "Salix aurita"
    client = gbif_client({a: (90001, a), b: (90002, b), c: (90003, c)})

    def generate(directory: Path, latin: list[str]) -> dict[str, str]:
        enrich([{"name_lat": name} for name in latin], client, directory)
        return {r["name_lat"]: r["id"] for r in rows(directory, "taxa.csv")}

    ids = generate(tmp_path, [a, b])
    assert generate(tmp_path / "fresh", [b, a]) == ids
    prior_names = {r["name"]: r["id"] for r in rows(tmp_path, "taxon_names.csv")}
    assert generate(tmp_path, [c, b])[b] == ids[b]
    assert (
        next(r for r in rows(tmp_path, "taxon_names.csv") if r["name"] == b)["id"]
        == prior_names[b]
    )
    assert generate(tmp_path, [a]) == {a: ids[a]}


def test_explicit_id_conflicts_are_rejected_before_replacing_results(
    tmp_path: Path,
) -> None:
    a, b, alias = "Salix alfa", "Salix beta", "Salix alfa L."
    client = gbif_client({a: (601, a), b: (602, b), alias: (601, a)})
    enrich([{"name_lat": alias}, {"id": 123, "name_lat": a}], client, tmp_path)
    before = (tmp_path / "taxa.csv").read_bytes()
    assert rows(tmp_path, "taxa.csv")[0]["id"] == "123"
    for explicit, name in [(124, a), (123, b)]:
        with pytest.raises(ValueError, match="taxon id"):
            enrich([{"id": explicit, "name_lat": name}], client, tmp_path)
        assert (tmp_path / "taxa.csv").read_bytes() == before


def test_gbif_key_reserved_by_another_taxon_requires_explicit_id(
    tmp_path: Path,
) -> None:
    a, b = "Salix alba", "Salix fragilis"
    client = gbif_client({a: (601, a), b: (602, b)})
    known = [{"id": 601, "name_lat": "Betula nana"}]
    source = [{"name_lat": a}, {"name_lat": b}]
    for ordered in [source, source[::-1], source[:1]]:
        with pytest.raises(ValueError, match="supply an unused id"):
            enrich(ordered, client, tmp_path, existing_taxa=known)
    enrich([{"id": 603, "name_lat": a}], client, tmp_path, existing_taxa=known)
    assert rows(tmp_path, "taxa.csv")[0]["id"] == "603"
