import pytest

from pipeline.normalize.persons import is_author_line, match_people, parse_people
from pipeline.normalize.phenophases import code_to_id, match_phenophase
from pipeline.normalize.places import match_place
from pipeline.normalize.taxa import match_taxon


# ------------------------------------------------------------------ таксоны
@pytest.mark.parametrize("raw,tid,method", [
    ("Береза пушистая", 4, "exact"),
    ("Герань лесная Geranium sylvaticum L.", 13, "latin"),
    ("Багульник болотный Ledum palustre L.", 11, "latin"),  # синоним
    ("Американская норка Neovison vison", 21, "latin"),  # синоним
    ("Заяц- беляк Lepus timidus", 18, "latin"),
    ("Pinus sylvestris\n(Сосна обыкновенная)", 5, "latin"),
    ("Зацветание герани лесной", 13, "lemma"),
    ("Березы пушистой", 4, "lemma"),
    ("лосей", 16, "lemma"),
    ("Первая встреча медведя", 17, "lemma"),
])
def test_taxon_match(raw, tid, method):
    m = match_taxon(raw)
    assert m.taxon_id == tid and m.method == method and not m.ambiguous


def test_taxon_head_is_ambiguous():
    m = match_taxon("Прилет лебедя")
    assert m.taxon_id == 26 and m.ambiguous


def test_taxon_genus_only():
    m = match_taxon("Salix sp.")
    assert m.ambiguous and m.method == "genus"


def test_taxon_fuzzy():
    m = match_taxon("Брусника обыкновеная")  # опечатка
    assert m.taxon_id == 8


def test_taxon_unmatched():
    m = match_taxon("Обыкновенная бурозубка (Sorex araneus)")
    assert m.unmatched


# ------------------------------------------------------------------ места
@pytest.mark.parametrize("raw,pid", [
    ("на озере Каскамаярви", 13),
    ("у р. Паз", 1),
    ("в окрестностях пос. Раякоски", 5),
    ("Калкупя, южнее озера Каскамаярви", 4),
    ("Подножие Калкупи", 4),
    ("о-ве Варлама", 3),
    ("за ИТС у устья Лауккуйоки", 24),
    ("на реке Ворьеме", 11),
    ("в заповеднике и окрестностях", 2),
    ("Урочище Латвала", 30),
    ("в п. Никеле", 35),
])
def test_place_match(raw, pid):
    assert match_place(raw).place_id == pid


def test_place_category_conflict():
    m = match_place("у озера Каскама")  # в каталоге Каскама — гора, озеро — Каскамаярви
    assert m.place_id is None and m.category_conflict


def test_place_unknown():
    assert match_place("Глухая плотина").place_id is None


# ------------------------------------------------------------------ люди
@pytest.mark.parametrize("raw,ids", [
    ("Хохлов, Сметанников, Шершнев", [2, 4, 3]),
    ("Крюкова С.А.", [7]),
    ("Поликарпова Н.В., Бычков Ю.М", [5, 11]),
    ("А. М. Хохлов, Е. В. Сметанников и В. Е. Шершнев", [2, 4, 3]),
    ("Валовой Е.В.", [8]),
    ("Дмитренко, 2020", [10]),
    ("Сметанникова", [15]),
    ("Сметанников", [4]),
    ("Макаровой О.А.", [1]),
])
def test_people(raw, ids):
    assert match_people(raw)[0] == ids


def test_person_unmatched():
    ids, refs = match_people("Зацаринный И.В.")
    assert ids == [] and refs[0].surname == "Зацаринный" and refs[0].person_id is None


def test_parse_people_initials():
    refs = parse_people("Поликарпова Н.В., Бычков Ю.М")
    assert [(r.surname, r.initials) for r in refs] == [("Поликарпова", "Н.В."), ("Бычков", "Ю.М.")]


def test_author_line():
    assert is_author_line("Поликарпова Н.В., Бычков Ю.М, Крюкова С.А., Воробьева Н.Г.")
    assert not is_author_line("Сезонная динамика растительных сообществ")


# ------------------------------------------------------------------ фенофазы
@pytest.mark.parametrize("text,group,code,applies", [
    ("зацветание", "сосудистые растения", "5", "plants"),
    ("массовое цветение", None, "6", "plants"),
    ("фаза 12 - начало изменения окраски листьев", None, "12", "plants"),
    ("5 (BBCH 60) - начало цветения", None, "5", "plants"),
    ("фенофаза 10б", None, "10б", "plants"),
    ("начало листопада", None, "14", "plants"),
    ("Прилет лебедя", "птицы", "arrival", "animals"),
    ("Первая песня большой синицы", "птицы", "first_song", "animals"),
    ("Первое кукование кукушки", "птицы", "first_song", "animals"),
    ("выход из берлоги", "млекопитающие", "first_seen_spring", "animals"),
    ("Гоголь — кладка яиц", "птицы", "breeding", "animals"),
    ("обнаружен новорожденный лосенок", "млекопитающие", "young", "animals"),
    ("Последняя встреча лебедя", "птицы", "departure", "animals"),
    ("Белка в летнем меху", "млекопитающие", "moult_summer", "animals"),
])
def test_phenophase(text, group, code, applies):
    pid, _ = match_phenophase(text, group=group)
    assert pid == code_to_id(code, applies)


def test_phenophase_none():
    assert match_phenophase("учёт на маршруте", group="млекопитающие")[0] is None
