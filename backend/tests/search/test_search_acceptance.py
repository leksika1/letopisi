"""4 приёмочных запроса жюри (лист «примеры запросов» parameters_2020.xlsx и
contracts/fixtures/saved_queries.json) — через фильтры и через /query/parse по исходному тексту.

На текущих данных:
- книга 1995 — только ручная расшифровка разворота PDF 40 (фикстуры 34–38, ЗМУ);
- книга 1996 — записей нет совсем (сканы ещё не обработаны) → всё, что относится к 1996, ожидаемо пусто;
- книга 2020 — 33 записи фикстур + 572 записи снимка ядра (страницы 69–79, 449–455).
Тест каждый раз отдельно проверяет, что пустой результат объясняется отсутствием данных, а не поиском:
при добавлении синтетической записи соответствующий запрос её находит.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.app.search import parse_query, search

ROOT = Path(__file__).resolve().parents[3]

FIXTURE_LOS = {19, 23, 24, 25, 26, 27, 28, 29, 37}  # лось в фикстурах: 8 × 2020 + 1 × 1995 (30 — 2019 год)
SNAP_LOS = {30000557}  # «Лось Alces alces» (учёт, раздел 8.5) из снимка ядра
FIXTURE_ZMU_1995 = {34, 35, 36, 37, 38}  # ЗМУ 1995: Хохлов, Сметанников, Шершнев
FIXTURE_GERANIUM_5 = {1, 2, 3, 5, 6}  # табл. 7.10 (2018–2020), календарь природы, табл. 7.11
SNAP_GERANIUM_5 = {30000078, 30000079, 30000080, 30000358, 30000527}


def _ids(res) -> set[int]:
    return {i["id"] for i in res["items"]}


def _n(conn, sql: str, **p) -> int:
    return int(conn.execute(text(sql), p).scalar_one())


def _xlsx_queries() -> dict[int, str] | None:
    cands = [os.environ.get("LETOPISI_RAW_DIR"), ROOT / "data" / "raw", ROOT.parent / "letopisi" / "data" / "raw"]
    for c in cands:
        if c and (Path(c) / "parameters_2020.xlsx").exists():
            import openpyxl

            wb = openpyxl.load_workbook(Path(c) / "parameters_2020.xlsx", read_only=True)
            out = {}
            for a, b in wb["примеры запросов"].iter_rows(values_only=True):
                if isinstance(a, str) and a.startswith("Запрос ") and b:
                    out[int(a.split()[1])] = b.strip()
            return out
    return None


def test_xlsx_texts_equal_saved_queries(saved_queries):
    xq = _xlsx_queries()
    if xq is None:
        pytest.skip("нет data/raw/parameters_2020.xlsx (задайте LETOPISI_RAW_DIR)")
    assert sorted(xq) == [1, 2, 3, 4]
    for i, t in xq.items():
        assert t == saved_queries[i]["text"]


def _texts(saved_queries) -> dict[int, str]:
    return _xlsx_queries() or {i: q["text"] for i, q in saved_queries.items()}


# --------------------------------------------------------------------------- 1. Макарова и Сметанников 1995–1996

def test_q1_filters(conn, saved_queries):
    res = search(conn, saved_queries[1]["filters"], page_size=500)
    assert _ids(res) == FIXTURE_ZMU_1995
    assert res["total"] == 5
    assert all(i["labels"]["book_year"] == 1995 for i in res["items"])
    assert all(4 in i["person_ids"] for i in res["items"])  # Сметанников (id 4), не Сметанникова (15)
    # хронология: сначала 30.03, затем 31.03, запись без даты («в конце зимы») — в конце года
    assert [i["date_from"] for i in res["items"]][:4] == ["1995-03-30"] * 3 + ["1995-03-31"]
    assert res["items"][-1]["id"] == 37


def test_q1_empty_parts_are_missing_data(conn, saved_queries, add_obs):
    flt = dict(saved_queries[1]["filters"])
    # Макарова за 1995–1996: ни одной записи — в БД она есть только соавтором публикаций тома 2020
    # (31 — 2020 г., 33 — 2018 г.), наблюдений 1995–1996 с её участием нет → данных нет
    assert search(conn, {**flt, "person": ["Макарова"]})["total"] == 0
    assert _n(conn, "SELECT count(*) FROM observations WHERE observers_raw ILIKE '%макаров%'") == 0
    assert _n(conn, "SELECT count(*) FROM observation_persons op JOIN observations o ON o.id = op.observation_id "
                    "WHERE op.person_id = 1 AND o.obs_year BETWEEN 1995 AND 1996") == 0
    assert {i["id"] for i in search(conn, {"person": ["Макарова"]})["items"]} == {31, 33}
    # 1996: книга 3 не обработана — у документа 2 нет ни одной записи
    assert search(conn, {**flt, "doc_years": [1996], "years": [1996]})["total"] == 0
    assert _n(conn, "SELECT count(*) FROM observations WHERE document_id = 2") == 0
    # как только запись появится, запрос её находит (поиск не «слепой»)
    rid = add_obs(document_id=2, obs_year=1996, object_raw="Лось", taxon_id=16, observers_raw="Макарова Н.",
                  person_ids=[1], date_from="1996-02-10", date_to="1996-02-10", date_precision="day")
    res = search(conn, flt, page_size=500)
    assert rid in _ids(res) and res["total"] == 6


def test_q1_parse(conn, saved_queries):
    p = parse_query(conn, _texts(saved_queries)[1])
    f = p["filters"]
    assert f["person"] == ["Макарова", "Сметанников"]
    assert f["years"] == [1995, 1996]
    assert f["doc_years"] == [1995, 1996]
    assert "Макарова" in p["explanation"] and "Сметанников" in p["explanation"]
    assert not [w for w in p["warnings"] if w.startswith("Не распознано")]
    assert _ids(search(conn, f, page_size=500)) == FIXTURE_ZMU_1995


# --------------------------------------------------------------------------- 2. Лось 1995, 1996, 2020 по хронологии

def test_q2_filters(conn, saved_queries):
    res = search(conn, saved_queries[2]["filters"], page_size=500)
    assert _ids(res) == FIXTURE_LOS | SNAP_LOS
    assert res["total"] == 10
    years = [i["obs_year"] for i in res["items"]]
    assert years == sorted(years)  # 1995 → 2020
    assert 30 not in _ids(res)  # запись 2019 года
    dated = [i["date_from"] for i in res["items"] if i["date_from"]]
    assert dated == sorted(dated)
    assert [i["id"] for i in res["items"] if i["date_from"]] == [24, 25, 26, 23, 27, 28]
    assert all(i["record_type"] == "observation" for i in res["items"])


def test_q2_1996_is_missing_data(conn, saved_queries):
    assert search(conn, {**saved_queries[2]["filters"], "years": [1996]})["total"] == 0
    assert _n(conn, "SELECT count(*) FROM observations WHERE obs_year = 1996") == 0


def test_q2_parse(conn, saved_queries):
    p = parse_query(conn, _texts(saved_queries)[2])
    f = p["filters"]
    assert f["taxon_id"] == 16
    assert f["years"] == [1995, 1996, 2020]
    assert "doc_years" not in f
    assert f["record_type"] == "observation"
    assert (f["sort"], f["order"]) == ("date_from", "asc")
    assert any(w.startswith("2022 → 2020") for w in p["warnings"])
    assert "лось" in p["explanation"]
    res = search(conn, f, page_size=500)
    assert [i["id"] for i in res["items"]] == [i["id"] for i in search(conn, saved_queries[2]["filters"],
                                                                     page_size=500)["items"]]


# --------------------------------------------------------------------------- 3. Публикации о буром медведе

def test_q3_filters(conn, saved_queries):
    res = search(conn, saved_queries[3]["filters"], page_size=500)
    assert _ids(res) == {31}
    item = res["items"][0]
    assert item["record_type"] == "publication" and item["labels"]["book_year"] == 2020
    assert "медвед" in item["pub_title"].lower()


def test_q3_missing_and_text_fallback(conn, saved_queries, add_obs):
    flt = saved_queries[3]["filters"]
    assert search(conn, {**flt, "doc_years": [1995, 1996]})["total"] == 0
    assert _n(conn, "SELECT count(*) FROM observations WHERE record_type = 'publication' AND document_id IN (1, 2)") == 0
    # публикация без taxon_id находится по названию (запасной текстовый вариант)
    rid = add_obs(record_type="publication", object_type="publication", document_id=1, obs_year=1994,
                  object_raw="Экология бурого медведя на Кольском полуострове", pub_year=1994,
                  pub_title="Экология бурого медведя на Кольском полуострове")
    assert rid in _ids(search(conn, flt, page_size=500))


def test_q3_parse(conn, saved_queries):
    p = parse_query(conn, _texts(saved_queries)[3])
    f = p["filters"]
    assert f["taxon_id"] == 17
    assert f["record_type"] == "publication"
    assert f["doc_years"] == [1995, 1996, 2020]
    assert "years" not in f
    assert any(w.startswith("2022 → 2020") for w in p["warnings"])
    assert _ids(search(conn, f)) == {31}


# --------------------------------------------------------------------------- 4. Зацветание герани лесной

def test_q4_filters(conn, saved_queries):
    res = search(conn, saved_queries[4]["filters"], page_size=500)
    assert _ids(res) == FIXTURE_GERANIUM_5 | SNAP_GERANIUM_5
    assert all(i["taxon_id"] == 13 and i["phenophase_id"] == 5 for i in res["items"])
    # табл. 7.10 тома 2020: годы наблюдения 2018, 2019, 2020 — все из книги 2020 (doc_years, не years)
    assert {i["obs_year"] for i in res["items"]} == {2018, 2019, 2020}
    assert {i["labels"]["book_year"] for i in res["items"]} == {2020}
    dates = [i["date_from"] for i in res["items"]]
    assert dates == sorted(dates) and dates[0] == "2018-06-22"


def test_q4_missing_1995_1996(conn, saved_queries):
    assert search(conn, {**saved_queries[4]["filters"], "doc_years": [1995, 1996]})["total"] == 0
    assert _n(conn, "SELECT count(*) FROM observations WHERE taxon_id = 13 AND document_id IN (1, 2)") == 0


def test_q4_parse(conn, saved_queries):
    p = parse_query(conn, _texts(saved_queries)[4])
    f = p["filters"]
    assert f["taxon_id"] == 13
    assert f["phenophase_id"] == 5
    assert f["doc_years"] == [1995, 1996, 2020]
    assert "place_id" not in f and "place" not in f  # «в заповеднике „Пасвик“» = вся территория
    assert "заповедник" in p["explanation"]
    assert _ids(search(conn, f, page_size=500)) == FIXTURE_GERANIUM_5 | SNAP_GERANIUM_5


def test_print_acceptance_summary(conn, saved_queries, capsys):
    """Сводка для отчёта (видна с ``pytest -s``)."""
    from conftest import LOAD_REPORT

    with capsys.disabled():
        print("\nзагрузка:", LOAD_REPORT)
        for i, q in saved_queries.items():
            res = search(conn, q["filters"], page_size=500)
            ex = [(r["id"], r["object_raw"][:40], r["date_raw"], r["obs_year"]) for r in res["items"][:3]]
            print(f"запрос {i}: {res['total']} записей; примеры: {ex}")
