"""Эндпоинты роутера поиска (FastAPI TestClient) + phenology, places, suggest, query_parse.
Ответы сверяются со схемами contracts/observation.schema.json."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from backend.app.search import get_phenology, parse_query, place_species, search, suggest
from backend.app.search import query_parse as qp

SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "observation.schema.json")
                    .read_text(encoding="utf-8"))


def validator(name: str):
    return jsonschema.Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})


# --------------------------------------------------------------------------- GET /api/observations

def test_api_observations(client):
    r = client.get("/api/observations", params={"taxon": "лося", "years": "1995,1996,2020",
                                                "record_type": "observation", "sort": "date_from"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["total"] == 10 and body["page"] == 1 and body["page_size"] == 50
    v = validator("ObservationListItem")
    for it in body["items"]:
        v.validate(it)
    r = client.get("/api/observations", params={"person": "Макарова,Сметанников", "years": "1995-1996",
                                                "doc_years": "1995,1996"})
    assert r.json()["total"] == 5
    r = client.get("/api/observations?years=2018&years=2019&page_size=3&page=2")
    assert r.status_code == 200 and r.json()["page"] == 2 and len(r.json()["items"]) == 3
    r = client.get("/api/observations", params={"flagged": "true", "page_size": 500})
    assert all(i["flags"] for i in r.json()["items"])


@pytest.mark.parametrize("params", [{"years": "abc"}, {"page_size": "501"}, {"sort": "x; drop"},
                                    {"unknown": "1"}, {"date_from": "31.12.2020"}, {"group": "драконы"}])
def test_api_observations_errors(client, params):
    r = client.get("/api/observations", params=params)
    assert r.status_code == 422
    body = r.json()
    assert "error" in body or "detail" in body  # detail — встроенная валидация FastAPI (обработчик в main.py)
    if "error" in body:
        assert body["error"]["code"] == "validation_error" and body["error"]["message"]


# --------------------------------------------------------------------------- POST /api/query/parse

def test_api_query_parse(client, saved_queries):
    r = client.post("/api/query/parse", json={"text": saved_queries[2]["text"]})
    assert r.status_code == 200
    body = r.json()
    validator("QueryParseResponse").validate(body)
    assert body["filters"]["taxon_id"] == 16 and body["filters"]["years"] == [1995, 1996, 2020]
    assert client.post("/api/query/parse", json={"text": ""}).status_code == 422
    assert client.post("/api/query/parse", json={"txt": "лось"}).status_code == 422


def test_query_parse_rules(conn):
    p = parse_query(conn, "наблюдения Макаровой в 90-е годы")
    assert p["filters"]["person"] == ["Макарова"]
    assert p["filters"]["years"] == list(range(1990, 2000))
    p = parse_query(conn, "цветение берёзы с 2018 по 2020, сначала новые")
    f = p["filters"]
    assert f["taxon"].lower().startswith("берёз") and f["phenophase_id"] == 5
    assert f["years"] == [2018, 2019, 2020] and (f["sort"], f["order"]) == ("date_from", "desc")
    assert {i["taxon_id"] for i in search(conn, f, page_size=500)["items"]} <= {3, 4, None}
    p = parse_query(conn, "прилёт лебедя в 2020 году")
    assert p["filters"]["taxon_id"] == 26 and p["filters"]["phenophase_id"] == 18
    assert 14 in {i["id"] for i in search(conn, p["filters"])["items"]}
    p = parse_query(conn, "встречи медведя у горы Калкупя")
    assert p["filters"]["taxon_id"] == 17 and p["filters"]["place_id"] == 4
    p = parse_query(conn, "летопись за 2019 год")  # книги 2019 нет → ближайшая 2020
    assert p["filters"]["doc_years"] == [2020] and p["warnings"]
    p = parse_query(conn, "наблюдения в 2019 году")  # год наблюдения 2019 есть (табл. 7.10) → без замены
    assert p["filters"]["years"] == [2019] and not p["warnings"]
    p = parse_query(conn, "покажи всё")
    assert p["filters"] == {} and "не распознан" in p["explanation"].lower()


def test_query_parse_unknown_words_without_llm(conn):
    p = parse_query(conn, "все записи про сохатого в 2020 году")
    assert any("сохатого" in w for w in p["warnings"])
    assert p["filters"]["years"] == [2020]


def test_query_parse_llm_merge(conn, monkeypatch):
    calls = []

    def fake(query, ctx):
        calls.append((query, ctx))
        return {"taxon": "лось", "person": None, "place": "Калкупи", "phenophase": None, "years": [1999],
                "doc_years": None, "record_type": "observation", "group": None, "indicator": None,
                "date_from": None, "date_to": None, "q": None}

    monkeypatch.setattr(qp, "_llm_call", fake)
    p = parse_query(conn, "все записи про сохатого в 2020 году")
    assert calls and "сохатого" in calls[0][1]["unparsed_words"]
    f = p["filters"]
    assert f["taxon_id"] == 16 and f["place_id"] == 4
    assert f["years"] == [2020]  # правила приоритетнее LLM
    assert "языковой моделью" in p["explanation"]
    assert not any(w.startswith("Не распознано") for w in p["warnings"])


def test_query_parse_llm_unavailable_without_key(conn, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "yandex")
    monkeypatch.delenv("YC_API_KEY", raising=False)
    monkeypatch.delenv("YC_FOLDER_ID", raising=False)
    monkeypatch.setattr("pipeline.extract.llm.load_dotenv", lambda *a, **k: None)
    assert qp._llm_client() is None
    p = parse_query(conn, "все записи про сохатого")
    assert any("сохатого" in w for w in p["warnings"])


# --------------------------------------------------------------------------- GET /api/phenology

def test_phenology_table_7_10(conn, client):
    rows = get_phenology(conn, 13)
    v = validator("PhenologyRow")
    for r in rows:
        v.validate(r)
    zac = {(r["year"], r["date_from"]) for r in rows if r["phenophase_code"] == "5"}
    assert {(2018, "2018-06-22"), (2019, "2019-06-24"), (2020, "2020-06-19")} <= zac
    keys = [(r["phenophase_id"], r["year"]) for r in rows]
    assert keys == sorted(keys)  # ord фенофаз совпадает с id в фикстурах
    assert {r["year"] for r in get_phenology(conn, 13, years=[2018, 2019])} == {2018, 2019}
    assert {r["phenophase_id"] for r in get_phenology(conn, 13, phenophase_id=5)} == {5}
    # табл. 7.10 для другого вида: береза пушистая, зацветание по годам
    years = {r["year"] for r in get_phenology(conn, 4, phenophase_id=5)}
    assert years == {2018, 2019, 2020}
    r = client.get("/api/phenology", params={"taxon_id": 13, "phenophase_id": 5, "years": "2018,2019,2020"})
    assert r.status_code == 200 and len(r.json()) >= 3
    assert client.get("/api/phenology", params={"taxon_id": 99999}).status_code == 404
    assert client.get("/api/phenology").status_code == 422


# --------------------------------------------------------------------------- GET /api/places/{id}/species

def test_place_species(conn, client, add_obs):
    res = place_species(conn, 4)
    validator("PlaceSpecies").validate(res)
    by = {i["taxon_id"]: i for i in res["items"]}
    assert by[18]["n_records"] == 3 and by[18]["first_date"] == "1995-03-30" and by[18]["last_date"] == "1995-03-31"
    assert 17 in by and 16 in by
    assert {i["taxon_id"] for i in place_species(conn, 4, year=1995)["items"]} == {18}
    assert {i["group_ru"] for i in place_species(conn, 4, group="сосудистые растения")["items"]} == {"сосудистые растения"}
    rid = add_obs(object_raw="Глухарь", taxon_id=29, toponym_raw="склон Калкупи", place_id=None,
                  date_from="2020-09-01", date_to="2020-09-01")
    assert 29 in {i["taxon_id"] for i in place_species(conn, 4)["items"]} and rid
    r = client.get("/api/places/35/species")
    assert r.status_code == 200 and r.json()["place"]["lat"] == 69.41  # Никель — координаты места
    assert client.get("/api/places/99999/species").status_code == 404


# --------------------------------------------------------------------------- GET /api/suggest/{kind}

def test_suggest(conn, client):
    v = validator("SuggestItem")
    for kind, q, first in [("taxa", "лося", 16), ("taxa", "Alces", 16), ("taxa", "берёза пуш", 4),
                           ("taxa", "герань", 13), ("persons", "Сметанников", 4), ("persons", "Смет", 4),
                           ("places", "Калкупи", 4), ("places", "Никел", 35), ("phenophases", "зацветание", 5),
                           ("phenophases", "10а", 10), ("phenophases", "прилёт", 18)]:
        items = suggest(conn, kind, q)
        assert items and items[0]["id"] == first, (kind, q, items[:3])
        assert len(items) <= 10
        for it in items:
            v.validate(it)
    s = suggest(conn, "taxa", "Betula")
    assert {3, 4} <= {i["id"] for i in s[:2]}
    assert suggest(conn, "taxa", "лось")[0] == {"id": 16, "label": "Лось", "sublabel": "Alces alces"}
    p = suggest(conn, "persons", "Сметанникова")
    assert p[0]["id"] == 15
    assert suggest(conn, "phenophases", "5")[0]["label"].startswith("5 — начало цветения")
    assert len(suggest(conn, "taxa", "а", limit=3)) <= 3
    r = client.get("/api/suggest/taxa", params={"q": "лос"})
    assert r.status_code == 200 and r.json()[0]["id"] == 16
    assert client.get("/api/suggest/dragons", params={"q": "x"}).status_code == 400
    assert client.get("/api/suggest/taxa", params={"q": ""}).status_code == 400
    assert client.get("/api/suggest/taxa", params={"q": "x", "limit": 100}).status_code == 400
    assert client.get("/api/suggest/taxa", params={"q": "'; DROP TABLE taxa; --"}).status_code == 200
