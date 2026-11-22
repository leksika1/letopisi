"""Общие функции работы с текстом: нормализация для сравнения и лемматизация (pymorphy3)."""
from __future__ import annotations

import re
from functools import lru_cache

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")
_DASHES = "‐‑‒–—―−"


def fold(s: str | None) -> str:
    """Строка для сравнения: нижний регистр, ё→е, единые дефисы и кавычки, схлопнутые пробелы.

    Перенос «слово-\\nслово» склеивается, «Заяц- беляк» → «заяц-беляк».
    """
    if not s:
        return ""
    s = s.replace("­", "")
    for d in _DASHES:
        s = s.replace(d, "-")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)  # перенос строки внутри слова
    s = re.sub(r"(\w)-\s+(\w)", r"\1-\2", s)  # «Заяц- беляк»
    s = s.lower().replace("ё", "е")
    s = s.replace("«", '"').replace("»", '"').replace("„", '"').replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def words(s: str) -> list[str]:
    """Слова (кириллица/латиница, с внутренними дефисами)."""
    return _WORD_RE.findall(s or "")


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=200_000)
def lemma(word: str) -> str:
    """Начальная форма слова (для кириллицы — pymorphy3, латиница — как есть в нижнем регистре).

    Прилагательные приводятся к мужскому роду ед. ч. («лесная» → «лесной»), поэтому сравнивать
    нужно леммы обеих сторон.
    """
    w = fold(word)
    if not w or not re.search(r"[а-я]", w):
        return w
    if "-" in w:
        return "-".join(lemma(p) for p in w.split("-") if p)
    return _morph().parse(w)[0].normal_form.replace("ё", "е")


def lemmas(s: str) -> tuple[str, ...]:
    return tuple(lemma(w) for w in words(s))


def has_cyrillic(s: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(s: str | None) -> list[str]:
    """Числа из строки в нормализованном виде («3,58» → «3.58», «7.50» → «7.5»)."""
    out = []
    for m in _NUM_RE.findall(s or ""):
        v = m.replace(",", ".")
        if "." in v:
            v = v.rstrip("0").rstrip(".") or "0"
        v = v.lstrip("0") or "0" if "." not in v else v
        out.append(v)
    return out


def parse_number(s: str | None) -> float | None:
    """Первое число строки как float (запятая — десятичный разделитель)."""
    m = _NUM_RE.search(s or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


NUMBER_WORDS = {
    "один": 1, "одна": 1, "одно": 1, "единожды": 1, "два": 2, "две": 2, "пара": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "пятнадцать": 15, "двадцать": 20,
}


def word_number(s: str | None) -> float | None:
    """Число, записанное словом («Четыре лося» → 4)."""
    for w in words(s or ""):
        v = NUMBER_WORDS.get(fold(w))
        if v is not None:
            return float(v)
    return None
