"""Парсер табл. 7.10, подписи к фото и разделы — на реальном тексте книги 2020 (пропуск без PDF)."""
import json

from pipeline.extract.captions import parse_caption
from pipeline.extract.router import route
from pipeline.extract.tables.common import TableContext, parse_row
from pipeline.extract.tables.parsers import detect, parse_year_columns
from pipeline.normalize.dicts import FIXTURES
from pipeline.sections import build_sections, parse_toc

from .conftest import needs_pdf


def test_parse_row():
    t = "№: 7.; Вид: Береза пушистая Betula pubescens Ehrh.; 2020: 03.06; 2019: 11.06; 2018: 18.05"
    cells = parse_row(t)
    assert [c.key for c in cells] == ["№", "Вид", "2020", "2019", "2018"]
    assert all(t[c.vstart:c.end] == c.value for c in cells)
    assert parse_row("Болото верховое сфагновое") is None
    assert detect(cells) == "year_columns"


def test_year_columns_synthetic():
    t = "№: 2.; Вид: Мать-и-мачеха Tussilago farfara L.; 2020: 28.05; 2019: —; 2018: 02.05"
    ctx = TableContext(caption="Таблица 7.10 Сравнительная хронология зацветания высших растений 2018 - 2020 гг.")
    drafts = parse_year_columns(t, parse_row(t), ctx)
    assert [(d.fields["obs_year"], d.fields["date_raw"]) for d in drafts] == [(2020, "28.05"), (2018, "02.05")]
    for d in drafts:
        assert t[d.quote_span[0]:d.quote_span[1]] == d.quote
        assert d.fields["phenophase_raw"] == "зацветание"


def test_caption():
    t = "Рисунок 8.483 - Обыкновенная бурозубка (Sorex araneus). О. Варлама, 29.07.2020 г. Фото: Валова \nЕ.В. "
    drafts, done = parse_caption(t)
    assert done and len(drafts) == 1
    f = drafts[0].fields
    assert f["object_raw"] == "Обыкновенная бурозубка (Sorex araneus)"
    assert f["toponym_raw"] == "О. Варлама" and f["date_raw"] == "29.07.2020 г." and f["observers_raw"] == "Валова Е.В."


def test_caption_landscape_skipped():
    drafts, done = parse_caption("Рисунок 8.486 - Западный берег озера Куэтсъярви. Березовый лес (линия 1). Фото: Крюкова С.А.")
    assert done and drafts == []


@needs_pdf
def test_table_710_real(chunks_69_70):
    chunks, _ = chunks_69_70
    routed = route(chunks, {}, {}, set(), 2020)
    drafts = [d for r in routed for d in r.drafts if d.strategy == "table:year_columns"]
    by = {(d.fields["object_raw"], d.fields["obs_year"]): d.fields["date_raw"] for d in drafts}
    assert by[("Ива козья Salix caprea L.", 2018)] == "12.05"
    assert by[("Береза пушистая Betula pubescens Ehrh.", 2019)] == "11.06"
    assert ("Мать-и-мачеха Tussilago farfara L.", 2019) not in by  # прочерк — нет данных
    # продолжение таблицы на стр. 70 без шапки
    assert by[("Вахта трехлистная Menyanthes trifoliata L.", 2020)] == "18.06"
    # рисунок внутри ячейки — отдельная запись-фото, из названия вида он вырезан
    assert ("Багульник болотный Ledum palustre L.", 2020) in by
    caps = [d for r in routed for d in r.drafts if d.strategy == "caption"]
    assert any(d.fields["toponym_raw"] == "Болото Мениккасаари" for d in caps)
    rows = sum(1 for r in routed if r.strategy == "table:year_columns")
    assert rows == 28


@needs_pdf
def test_sections_toc_real():
    from pipeline.devtools.quick_chunks import dump
    from .conftest import PDF_2020

    chunks, _ = dump(PDF_2020, 3, (1, 80))
    res = build_sections(3, chunks, 732)
    assert res.mode == "toc" and res.toc_pages == {3, 4}
    s73 = next(s for s in res.sections if s["code"] == "7.3")
    assert (s73["pdf_page_from"], s73["title"]) == (69, "Сезонная динамика растительных сообществ")
    assert s73["pdf_page_to"] in (78, 79)
    assert next(s for s in res.sections if s["id"] == s73["parent_id"])["code"] == "7"
    a = res.authors[s73["id"]]
    assert a["authors_raw"] == "Поликарпова Н.В., Бычков Ю.М, Крюкова С.А., Воробьева Н.Г."
    assert a["person_ids"] == [5, 11, 7, 6]
    s42 = next(s for s in res.sections if s["code"] == "4.2")
    assert s42["title"] == "Даты начала сезонов, продолжительность, температура воздуха"
    c69 = [c for c in chunks if c["locator"]["pdf_page"] == 70]
    assert all(res.chunk_section[c["id"]] == s73["id"] for c in c69)


def test_parse_toc_wrapped():
    text = "СОДЕРЖАНИЕ\n2. Пробные и учетные площади, постоянные\n9\n(временные) маршруты\n3. Ландшафты и почвы\n10\n" \
           "9. Состояние заповедного режима\nна природу заповедника\n9.1 Состояние режима\n521\nПриложения\n611- 732"
    e = parse_toc(text)
    assert [(x.code, x.printed_page) for x in e] == [("2", 9), ("3", 10), ("9", 521), ("9.1", 521), (None, 611)]
    assert e[0].title.endswith("(временные) маршруты") and e[2].title.endswith("на природу заповедника")


def test_sections_generic_scan():
    """Без оглавления: ручная расшифровка скана 1995 (fixtures/chunks.jsonl)."""
    chunks = [json.loads(x) for x in (FIXTURES / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    chunks = [c for c in chunks if c["document_id"] == 1]
    res = build_sections(1, chunks, 66)
    assert res.mode == "headings"
    s = res.sections[0]
    assert s["title"].startswith("Млекопитающие. Результаты зимнего маршрутного учета зверей и птиц")
    assert res.authors[s["id"]]["person_ids"] == [2, 4, 3]
    assert all(res.chunk_section[c["id"]] == s["id"] for c in chunks)
