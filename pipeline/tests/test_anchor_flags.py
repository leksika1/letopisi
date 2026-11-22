from pipeline.anchor import anchor, find_span
from pipeline.flags import compute_flags, confidence, value_in_quote

TEXT = "21.05 \nЧетыре лося за ИТС у Каскамаярви, все самцы, 3 взрослых и 1 молодой 2-х \nлетний телёнок. Есть фото (Поликарпова Н.В.)"
LINES = [[0, 6, 0.1, 0.1, 0.2, 0.12], [7, 81, 0.1, 0.13, 0.9, 0.15], [82, 126, 0.1, 0.16, 0.6, 0.18]]


def test_exact():
    a = anchor("Четыре лося за ИТС у Каскамаярви", TEXT, LINES)
    assert a.method == "exact" and TEXT[a.span[0]:a.span[1]] == a.quote
    assert a.bboxes == [[0.1, 0.13, 0.9, 0.15]]


def test_normalized_whitespace_and_yo():
    a = anchor("3 взрослых и 1 молодой 2-х летний теленок", TEXT, LINES)
    assert a.method == "normalized"
    assert TEXT[a.span[0]:a.span[1]] == a.quote  # цитата заменена дословной подстрокой
    assert "\n" in a.quote and len(a.bboxes) == 2


def test_hyphen_break():
    text = "наблюдали красно-\nсерых полевок (Myodes rufocanus)"
    span, method, _ = find_span("наблюдали красносерых полевок", text)
    assert method == "normalized" and text[span[0]:span[1]].startswith("наблюдали")


def test_fuzzy():
    a = anchor("Четыре лося за ИТС у Каскамаярви все самцы 3 взрослых", TEXT, LINES)
    assert a.method in ("fuzzy", "normalized") and a.found
    assert TEXT[a.span[0]:a.span[1]] == a.quote


def test_not_found():
    a = anchor("Медведь вышел из берлоги на Калкупе", TEXT, LINES)
    assert not a.found and a.span is None and a.bboxes == []


def test_chunk_bbox_fallback():
    a = anchor("Четыре лося", TEXT, None, [0.1, 0.1, 0.9, 0.2])
    assert a.bboxes == [[0.1, 0.1, 0.9, 0.2]]


def test_value_in_quote():
    assert value_in_quote("3,58", 3.58, "Показатель учета – 3,58 следов/10 км")
    assert not value_in_quote("3,17", 3.17, "Показатель учета – 3,58 следов/10 км")
    assert value_in_quote("Четыре", 4, TEXT)
    assert value_in_quote("7.50", 7.5, "Заяц-беляк | 9 | 7.50 | 4 | 4.0")


def _flags(obs, **kw):
    base = dict(doc_year=2020, quote_found=True, date_ambiguous=False, taxon_unmatched=False, taxon_ambiguous=False,
                place_unmatched=False, person_unmatched=False, table_uncertain=False, ocr_confidence=None)
    base.update(kw)
    return compute_flags(obs, **base)


def test_flags_and_confidence():
    obs = {"quote": "Показатель учета – 3,58 следов/10 км", "value_raw": "3,17", "value_num": 3.17,
           "date_raw": "12.03", "obs_year": 2019, "object_type": "taxon"}
    f = _flags(obs, taxon_ambiguous=True, ocr_confidence=0.5)
    assert f == ["value_not_in_quote", "date_not_in_quote", "taxon_ambiguous", "year_outside_book", "low_ocr"]
    assert confidence("llm", f) == 0.1


def test_year_outside_book_iff():
    assert "year_outside_book" not in _flags({"obs_year": 2020, "object_type": "taxon", "quote": "x"})
    assert "year_outside_book" in _flags({"obs_year": 2018, "object_type": "taxon", "quote": "x"})


def test_quote_not_found_flag():
    f = _flags({"quote": "x", "obs_year": 2020, "object_type": "taxon", "value_raw": "5"}, quote_found=False)
    assert f == ["quote_not_found"]
    assert confidence("llm", f) == 0.5


def test_table_confidence():
    assert confidence("table:year_columns", []) == 0.95
    assert confidence("table:counts", ["table_parse_uncertain"]) == 0.75
    assert confidence("table:year_columns", [], observers_from_section=True) == 0.9
