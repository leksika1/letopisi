import pytest

from pipeline.normalize.dates import date_in_text, parse_date

CASES = [
    # raw, hint, from, to, precision, ambiguous
    ("12.05", 2020, "2020-05-12", "2020-05-12", "day", False),
    ("03.06.2020 г.", 1995, "2020-06-03", "2020-06-03", "day", False),
    ("20.10.19", 2020, "2019-10-20", "2019-10-20", "day", False),
    ("11.04.96", 2020, "1996-04-11", "1996-04-11", "day", False),
    ("12 мая", 2020, "2020-05-12", "2020-05-12", "day", False),
    ("16 октября 2019 г.", 2020, "2019-10-16", "2019-10-16", "day", False),
    ("30.04 (10:30)", 2020, "2020-04-30", "2020-04-30", "day", False),
    ("12-15.06", 2020, "2020-06-12", "2020-06-15", "range", False),
    ("07 – 08.09.2020", None, "2020-09-07", "2020-09-08", "range", False),
    ("07.09. – 10.09.2020", None, "2020-09-07", "2020-09-10", "range", False),
    ("с 30 по 31 марта", 1995, "1995-03-30", "1995-03-31", "range", False),
    ("с 01 по 31 мая", 2020, "2020-05-01", "2020-05-31", "range", False),
    ("с 25.06.2020 по 30.06.2020", None, "2020-06-25", "2020-06-30", "range", False),
    ("30 марта – 2 апреля", 1995, "1995-03-30", "1995-04-02", "range", False),
    ("апрель-июнь 2020", None, "2020-04-01", "2020-06-30", "range", False),
    ("в первой декаде июня", 2020, "2020-06-01", "2020-06-10", "decade", False),
    ("во второй декаде мая", 2020, "2020-05-11", "2020-05-20", "decade", False),
    ("в третьей декаде февраля", 2020, "2020-02-21", "2020-02-29", "decade", False),
    ("в конце мая", 2020, "2020-05-21", "2020-05-31", "decade", False),
    ("в начале июня", 2020, "2020-06-01", "2020-06-10", "decade", False),
    ("середина июля", 2020, "2020-07-11", "2020-07-20", "decade", False),
    ("в мае", 2020, "2020-05-01", "2020-05-31", "month", False),
    ("в июле 2020 г.", None, "2020-07-01", "2020-07-31", "month", False),
    ("весной", 2020, "2020-03-01", "2020-05-31", "season", False),
    ("летом 2020 г.", None, "2020-06-01", "2020-08-31", "season", False),
    ("осенью", 1996, "1996-09-01", "1996-11-30", "season", False),
    ("зимой 1995/96", None, "1995-12-01", "1996-02-29", "season", False),
    ("1995", None, "1995-01-01", "1995-12-31", "year", False),
]


@pytest.mark.parametrize("raw,hint,d_from,d_to,prec,amb", CASES)
def test_parse(raw, hint, d_from, d_to, prec, amb):
    r = parse_date(raw, hint)
    assert (r.date_from, r.date_to, r.precision) == (d_from, d_to, prec)
    assert r.ambiguous is amb


def test_day_from_equals_to():
    r = parse_date("19.06", 2020)
    assert r.date_from == r.date_to


def test_year_from_text_overrides_hint():
    r = parse_date("08.11.19", 2020)
    assert r.year == 2019 and r.year_from_text


def test_hint_year_used():
    r = parse_date("19.06", 2018)
    assert r.year == 2018 and not r.year_from_text


def test_winter_without_year_is_ambiguous():
    r = parse_date("зимой", 2020)
    assert r.ambiguous and r.date_from == "2019-12-01"


def test_end_of_winter_season():
    r = parse_date("в конце зимы", 1995)
    assert r.precision == "season" and r.date_from == "1995-02-01"


def test_extra_text_is_ambiguous():
    r = parse_date("30 марта 1995 года … В первый и второй день учета", None)
    assert r.date_from == "1995-03-30" and r.ambiguous


def test_several_dates():
    r = parse_date("12.06, 15.06 и 20.06", 2020)
    assert r.ambiguous and r.date_from == "2020-06-12" and r.date_to == "2020-06-20"


def test_invalid_date():
    r = parse_date("31.06", 2020)
    assert not r.ok and r.ambiguous


def test_no_year_anywhere():
    r = parse_date("12.05", None)
    assert not r.ok and r.ambiguous


def test_empty():
    r = parse_date(None)
    assert not r.ok and not r.ambiguous


def test_date_in_text():
    assert date_in_text("19.06", "Герань 19.06 24.06")
    assert date_in_text("с 30 по 31 марта", "учёт шёл с 30 по\n31 марта 1995")
    assert not date_in_text("21.06", "Герань 19.06")
