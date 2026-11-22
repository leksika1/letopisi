"""Фильтры search(): падежи и синонимы, род, люди, места, даты, годы, пагинация, сортировка,
flagged, total, rejected, инъекции. Плюс разбор query-параметров (без БД)."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from backend.app.search import FilterError, SearchFilters, parse_query_params, search
from backend.app.search import resolve as R
from backend.app.search.filters import SearchRequest, parse_year_list


def ids(conn, **flt) -> set[int]:
    return {i["id"] for i in search(conn, flt, page_size=10_000)["items"]}


def total(conn, **flt) -> int:
    return search(conn, flt, page_size=1)["total"]


# --------------------------------------------------------------------------- query-параметры (без БД)

def test_parse_year_list():
    assert parse_year_list("1995,1996") == [1995, 1996]
    assert parse_year_list("1995-1997, 2020") == [1995, 1996, 1997, 2020]
    assert parse_year_list("1996–1995") == [1995, 1996]
    assert parse_year_list(["1995", "2020"]) == [1995, 2020]
    with pytest.raises(ValueError):
        parse_year_list("девяносто")
    with pytest.raises(ValueError):
        parse_year_list("1000-3000")


def test_parse_query_params():
    r = parse_query_params({"years": "1995-1996", "doc_years": "1995,1996", "person": "Макарова, Сметанников",
                            "flagged": "true", "page": "2", "page_size": "10", "sort": "obs_year",
                            "order": "desc", "date_from": "2020-01-01", "taxon": " лось ", "format": "csv"})
    assert isinstance(r, SearchRequest)
    assert r.years == [1995, 1996] and r.doc_years == [1995, 1996]
    assert r.person == ["Макарова", "Сметанников"]
    assert r.flagged is True and r.page == 2 and r.page_size == 10
    assert r.date_from == date(2020, 1, 1) and r.taxon == "лось"
    assert r.filters().to_json()["years"] == [1995, 1996]
    for bad in ({"years": "abc"}, {"unknown": "1"}, {"sort": "drop table"}, {"page_size": "100000"},
                {"date_from": "2020-13-01"}, {"flagged": "может быть"}, {"record_type": "x"},
                {"date_from": "2020-02-01", "date_to": "2020-01-01"}):
        with pytest.raises(FilterError):
            parse_query_params(bad)


def test_search_filters_contract():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchFilters.model_validate({"page": 1})  # additionalProperties: false
    f = SearchFilters(years=[1996, 1995, 1995])
    assert f.years == [1995, 1996]


# --------------------------------------------------------------------------- вид: падежи, синонимы, род

def test_taxon_cases_and_latin(conn):
    base = ids(conn, taxon="лось")
    assert base and {19, 23, 37, 30000557} <= base
    for s in ("лося", "лосем", "Лось", "Alces alces", "alces alces", "лосиха"):
        assert ids(conn, taxon=s) == base, s
    assert ids(conn, taxon_id=16) == base


def test_taxon_resolution_steps(conn):
    assert R.resolve_taxon(conn, "лося").ids == [16]
    assert R.resolve_taxon(conn, "берёзы пушистой").ids == [4]
    assert R.resolve_taxon(conn, "Betula pubescens Ehrh.").ids == [4]
    assert R.resolve_taxon(conn, "Ledum palustre").ids == [11]  # синоним
    assert R.resolve_taxon(conn, "буром медведе").ids == [17]  # «буром» = «бурый», не «бур»
    r = R.resolve_taxon(conn, "Betula")
    assert r.ids == [3, 4] and r.method == "genus"
    r = R.resolve_taxon(conn, "берёза")
    assert r.ids == [3, 4] and r.method == "genus_ru"
    assert R.resolve_taxon(conn, "Vaccinium").ids == [7, 8, 9]
    r = R.resolve_taxon(conn, "Герань леснаяя")  # опечатка → pg_trgm
    assert r.ids == [13] and r.method == "trgm"
    r = R.resolve_taxon(conn, "млекопитающие")
    assert r.method == "group" and 16 in r.ids and 4 not in r.ids


def test_taxon_yo_and_genus(conn):
    assert ids(conn, taxon="берёза") == ids(conn, taxon="береза") == ids(conn, taxon="Betula")
    betula = search(conn, {"taxon": "Betula"}, page_size=10_000)["items"]
    assert {i["taxon_id"] for i in betula} <= {3, 4, None}
    assert {3, 4} <= {i["taxon_id"] for i in betula}
    pub = ids(conn, taxon="берёзы пушистой")
    assert pub and pub < ids(conn, taxon="Betula")
    assert ids(conn, taxon="Береза пушистая") == pub


def test_taxon_text_fallback_for_null_taxon(conn, add_obs):
    rid = add_obs(object_raw="Свежие следы лося у дороги", taxon_id=None, obs_year=2021)
    rid2 = add_obs(object_raw="Берёза пушистая (подрост)", taxon_id=None)
    assert rid in ids(conn, taxon="лось") and rid in ids(conn, taxon_id=16)
    assert rid2 in ids(conn, taxon="береза пушистая") and rid2 in ids(conn, taxon="Betula")
    # запасной вариант не трогает записи с другим taxon_id
    rid3 = add_obs(object_raw="Лось? нет — северный олень", taxon_id=25)
    assert rid3 not in ids(conn, taxon="лось")
    # вид, которого нет в справочнике (снимок ядра: taxon_id NULL)
    n = ids(conn, taxon="Подбел многолистный")
    assert len(n) >= 20 and ids(conn, taxon="Andromeda polifolia") == n


def test_group_filter(conn):
    items = search(conn, {"group": "птицы"}, page_size=10_000)["items"]
    assert items and all(i["labels"]["taxon_group"] == "птицы" for i in items)


# --------------------------------------------------------------------------- люди

def test_person_cases_and_gender(conn, add_obs):
    f_ = add_obs(document_id=2, obs_year=1996, object_raw="Ива козья", observers_raw="Сметанникова Л.А.",
                 person_ids=[15])
    m_ = add_obs(document_id=2, obs_year=1996, object_raw="Лось", observers_raw="Н. Макаровой")  # без person_ids
    zmu = {34, 35, 36, 37, 38}
    assert ids(conn, person=["Сметанников"]) == zmu  # не находит Сметанникову
    assert ids(conn, person=["Сметанникова"]) == {f_}
    mak = {m_, 31, 33}  # 31, 33 — публикации тома 2020, Макарова среди авторов
    assert ids(conn, person=["Макарова"]) == mak  # m_ — только по тексту «Н. Макаровой»
    assert ids(conn, person=["Макаровой"]) == mak
    assert ids(conn, person=["Макарова и Сметанников"]) == zmu | mak
    assert ids(conn, person=["Макарова", "Сметанников"]) == zmu | mak
    assert R.resolve_persons(conn, ["Сметанникову"]).ids == [4]
    assert R.resolve_persons(conn, ["Хохлова"]).ids == [2]  # «Хохлова» (род. п.) → Хохлов: Хохловой в каталоге нет


def test_person_section_author_fallback(conn, add_obs):
    conn.execute(text("INSERT INTO sections (id, document_id, code, title, authors_raw, author_person_ids) "
                      "VALUES (99001, 2, '8.5', 'Млекопитающие', 'Макарова О.А., Хохлов А.М.', '{}')"))
    rid = add_obs(document_id=2, obs_year=1996, section_id=99001, object_raw="Росомаха", observers_raw=None)
    rid2 = add_obs(document_id=2, obs_year=1996, section_id=99001, object_raw="Росомаха",
                   observers_raw="Шершнев")  # наблюдатель назван явно → автор раздела не подставляется
    got = ids(conn, person=["Макарова"])
    assert rid in got and rid2 not in got


def test_person_in_pub_authors(conn):
    assert 33 in ids(conn, person=["Поликарпова"], record_type="publication")


# --------------------------------------------------------------------------- места

def test_place_cases(conn, add_obs):
    base = ids(conn, place_id=4)
    assert {22, 23, 34, 35, 36, 38} <= base
    for s in ("Калкупя", "Калкупи", "горы Калкупя", "г. Калкупя"):
        assert ids(conn, place=s) == base, s
    rid = add_obs(object_raw="Глухарь", taxon_id=29, toponym_raw="северный склон Калкупи", place_id=None)
    assert rid in ids(conn, place="Калкупя") and rid in ids(conn, place_id=4)
    rid2 = add_obs(object_raw="Глухарь", taxon_id=29, toponym_raw="у озера Каскамаярви", place_id=None)
    assert rid2 not in ids(conn, place="Каскама")  # гора Каскама ≠ озеро Каскамаярви
    assert R.resolve_place(conn, "Мениккайоки").ids == [12]


# --------------------------------------------------------------------------- фенофазы (разрешение строки)

def test_phenophase_resolution(conn):
    assert R.resolve_phenophase(conn, "зацветание").ids == [5]
    assert R.resolve_phenophase(conn, "цветение").ids == [5]
    assert R.resolve_phenophase(conn, "массовое цветение").ids == [6]
    assert R.resolve_phenophase(conn, "фаза 10а").ids == [10]
    assert R.resolve_phenophase(conn, "10б").ids == [11]
    assert R.resolve_phenophase(conn, "прилёт").ids == [18]
    assert R.resolve_phenophase(conn, "arrival").ids == [18]


# --------------------------------------------------------------------------- даты и годы

def test_date_interval_intersection(conn):
    june = ids(conn, date_from="2020-06-01", date_to="2020-06-30")
    assert 31 in june  # публикация с интервалом 2020-04-01…2020-06-30 пересекает июнь
    assert {21, 23, 27} <= june
    assert 19 not in june  # запись без дат исключается, когда задан диапазон
    assert 26 not in june  # 21.05
    # интервал «30–31 марта 1995» попадает и при запросе одного дня 31 марта
    assert {34, 35, 36} <= ids(conn, date_from="1995-03-31", date_to="1995-03-31")
    # только нижняя граница
    late = search(conn, {"date_from": "2020-10-01"}, page_size=10_000)["items"]
    assert late and all((i["date_to"] or i["date_from"]) >= "2020-10-01" for i in late)
    # без диапазона записи без дат остаются
    assert 19 in ids(conn, taxon_id=16)


def test_years_vs_doc_years(conn):
    y2018 = ids(conn, years=[2018])
    assert len(y2018) >= 70 and 3 in y2018
    assert ids(conn, doc_years=[2018]) == set()  # книги за 2018 нет
    assert ids(conn, doc_years=[2020], years=[2018]) == y2018
    assert ids(conn, doc_years=[1995]) == {34, 35, 36, 37, 38}
    assert 32 in ids(conn, years=[1995])  # публикация 1995 года из библиографии тома 2020
    assert 32 not in ids(conn, doc_years=[1995])
    assert ids(conn, years=[1995, 2018]) == ids(conn, years=[1995]) | y2018  # ИЛИ внутри массива


# --------------------------------------------------------------------------- пагинация, сортировка, total

def test_pagination_and_total(conn):
    flt = {"doc_years": [2020], "taxon": "Vaccinium"}
    full = search(conn, flt, page_size=10_000)
    n = full["total"]
    assert n == len(full["items"]) and n > 20
    got = []
    for p in range(1, n // 7 + 2):
        res = search(conn, flt, page=p, page_size=7)
        assert res["total"] == n and res["page"] == p and res["page_size"] == 7
        got += [i["id"] for i in res["items"]]
    assert got == [i["id"] for i in full["items"]]
    beyond = search(conn, flt, page=1000, page_size=7)
    assert beyond["items"] == [] and beyond["total"] == n
    with pytest.raises(FilterError):
        search(conn, flt, page_size=10_001)


def test_sorting(conn):
    items = search(conn, {"taxon_id": 16, "sort": "obs_year", "order": "desc"}, page_size=500)["items"]
    ys = [i["obs_year"] for i in items]
    assert ys == sorted(ys, reverse=True)
    items = search(conn, {"taxon_id": 16, "sort": "object_raw"}, page_size=500)["items"]
    first = [i["object_raw"].lower().replace("ё", "е")[0] for i in items]
    assert first == sorted(first)
    items = search(conn, {"taxon_id": 16, "sort": "date_from", "order": "desc"}, page_size=500)["items"]
    keys = [(i["date_from"] or f"{i['obs_year']}-12-31") for i in items]
    assert keys == sorted(keys, reverse=True)
    items = search(conn, {"taxon_id": 16, "sort": "id"}, page_size=500)["items"]
    assert [i["id"] for i in items] == sorted(i["id"] for i in items)
    # по умолчанию — хронология, NULL в конце, затем obs_year и id
    items = search(conn, {"doc_years": [2020], "record_type": "env_measurement"}, page_size=500)["items"]
    assert items[-1]["date_from"] is None or all(i["date_from"] for i in items)


def test_flagged_status_rejected(conn, add_obs):
    all_n = total(conn)
    t, f = total(conn, flagged=True), total(conn, flagged=False)
    assert t + f == all_n and t > 0 and f > 0
    assert all(i["flags"] for i in search(conn, {"flagged": True}, page_size=10_000)["items"])
    assert not any(i["flags"] for i in search(conn, {"flagged": False}, page_size=10_000)["items"])
    assert ids(conn, status="edited") == {6}
    assert ids(conn, status="verified") == {21, 23, 34}
    rid = add_obs(object_raw="Лось", taxon_id=16, status="rejected")
    assert rid not in ids(conn, taxon_id=16)
    assert ids(conn, status="rejected") == {rid}
    assert total(conn) == all_n


def test_other_filters(conn):
    assert ids(conn, record_type="publication") == {31, 32, 33}
    assert ids(conn, document_id=1) == {34, 35, 36, 37, 38}
    assert 38 in ids(conn, indicator="снег") or 38 in ids(conn, q="высота снега")
    assert ids(conn, q="берлога") == {22}
    assert {23} <= ids(conn, q="новорожденный лосёнок")
    assert ids(conn, phenophase_id=22) == {23}


def test_item_shape_and_labels(conn):
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "observation.schema.json")
                        .read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/ObservationListItem"})
    res = search(conn, {"doc_years": [1995, 2020]}, page_size=10_000)
    for it in res["items"]:
        v.validate(it)
    it = next(i for i in res["items"] if i["id"] == 34)
    assert it["labels"]["book_no"] == 2 and it["labels"]["book_year"] == 1995
    assert it["labels"]["persons"] == ["Хохлов", "Сметанников", "Шершнев"]
    assert it["labels"]["taxon_name_ru"] == "Заяц-беляк" and it["labels"]["taxon_group"] == "млекопитающие"
    assert it["labels"]["place_name"] == "Калкупя"
    assert it["person_ids"] == [2, 4, 3] and it["printed_pages"] == "76-77"
    assert it["locator"]["pdf_page"] == 40


# --------------------------------------------------------------------------- инъекции

EVIL = ["'; DROP TABLE observations; --", "лось' OR '1'='1", "%", "_", "\\", "(.*", "[", "a{1000000}",
        ":p1", "$$; SELECT 1; $$", "ё'\"", "))) OR 1=1 --"]


@pytest.mark.parametrize("s", EVIL)
def test_sql_injection_is_harmless(conn, s):
    before = conn.execute(text("SELECT count(*) FROM observations")).scalar_one()
    for key in ("taxon", "place", "q", "indicator"):
        res = search(conn, {key: s}, page_size=5)
        assert res["total"] <= before
    search(conn, {"person": [s]}, page_size=5)
    assert conn.execute(text("SELECT count(*) FROM observations")).scalar_one() == before
