from pipeline.extract.extractor import split_value


def test_unit_not_duplicated():
    assert split_value("10,1°С", None, "°С") == ("10,1", 10.1, "°С")
    assert split_value("25 ловушко-суток", None, "ловушко-сутки") == ("25", 25.0, "ловушко-сутки")


def test_unit_taken_from_tail_when_missing():
    assert split_value("15 особей", None, None) == ("15", 15.0, "особей")


def test_ranges_and_fractions_keep_raw_without_num():
    assert split_value("32/22", None, "особей") == ("32/22", None, "особей")
    assert split_value("10-15", None, "см") == ("10-15", None, "см")


def test_non_numeric_untouched():
    assert split_value("много", None, None) == ("много", None, None)
    assert split_value(None, None, None) == (None, None, None)
