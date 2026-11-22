"""Разбор русских дат летописей в интервал [date_from, date_to] с точностью date_precision.

Поддерживается:
- день: «12.05», «12.05.2020», «20.10.19», «12 мая», «16 октября 2019 г.», «30.04 (10:30)»;
- интервал: «12-15.06», «07 – 08.09.2020», «07.09. – 10.09.2020», «с 30 по 31 марта»,
  «с 25.06.2020 по 30.06.2020», «30 марта – 2 апреля», «апрель-июнь 2020»;
- декада: «в первой декаде июня», «в начале/середине/конце мая»;
- месяц: «в мае», «май 2020 г.»;
- сезон: «весной», «летом 2020 г.», «зимой 1995/96», «в конце зимы»;
- год: «1995», «в 2020 г.».

Год берётся из самой даты, иначе из ``year_hint`` (год столбца таблицы, раздела или книги).
Если дату удалось понять лишь частично или вариантов несколько — ``ambiguous = True``
(флаг ``date_ambiguous``).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
# Полные формы месяцев в любых падежах (родительный «мая», предложный «мае», именительный «май»).
_MONTH_RE = (
    r"(январ[ьяе]|феврал[ьяе]|марта?|марте|апрел[ьяе]|ма[йяе]|июн[ьяе]|июл[ьяе]|августа?|августе|"
    r"сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе])"
)

SEASONS = {  # (месяц начала, месяц конца, сдвиг года начала относительно «года сезона»)
    "зим": (12, 2, -1),
    "весн": (3, 5, 0),
    "лет": (6, 8, 0),
    "осен": (9, 11, 0),
}


def month_num(word: str) -> int | None:
    w = word.lower().replace("ё", "е")
    if w.startswith("ма") and len(w) <= 3:
        return 5
    for stem, n in MONTHS.items():
        if stem != "ма" and w.startswith(stem):
            return n
    return None


@dataclass
class DateResult:
    date_from: str | None = None
    date_to: str | None = None
    precision: str | None = None  # day/decade/month/season/year/range
    year: int | None = None  # год наблюдения, следующий из даты
    year_from_text: bool = False  # год был указан в самой дате
    ambiguous: bool = False
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.date_from is not None


def _year4(y: str) -> int:
    v = int(y)
    if len(y) <= 2:
        v += 2000 if v < 50 else 1900
    return v


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _clean(raw: str) -> str:
    s = raw.replace("ё", "е").replace("Ё", "Е")
    for d in "‐‑‒–—―−":
        s = s.replace(d, "-")
    s = re.sub(r"\(\s*\d{1,2}[:.]\d{2}\s*\)", " ", s)  # время «(10:30)»
    s = re.sub(r"\b\d{1,2}:\d{2}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_Y = r"(?:\.(\d{4}|\d{2}))?"
RE_DAY_NUM = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})" + _Y + r"(?![\d])\.?")
RE_DAY_WORD = re.compile(r"(?<!\d)(\d{1,2})\s+" + _MONTH_RE + r"(?:\s+(\d{4}))?", re.I)
RE_RANGE_NUM_SAME = re.compile(r"(?<![\d.])(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})" + _Y + r"(?!\d)")
RE_RANGE_NUM_FULL = re.compile(
    r"(?:с\s+)?(?<![\d.])(\d{1,2})\.(\d{1,2})" + _Y + r"\.?\s*(?:-|по|до)\s*(\d{1,2})\.(\d{1,2})" + _Y + r"(?!\d)",
    re.I,
)
RE_RANGE_WORD_SAME = re.compile(
    r"(?:с\s+)?(\d{1,2})\s*(?:-|по|до)\s*(\d{1,2})\s+" + _MONTH_RE + r"(?:\s+(\d{4}))?", re.I
)
RE_RANGE_WORD_FULL = re.compile(
    r"(?:с\s+)?(\d{1,2})\s+" + _MONTH_RE + r"\s*(?:-|по|до)\s*(\d{1,2})\s+" + _MONTH_RE + r"(?:\s+(\d{4}))?", re.I
)
RE_MONTH_RANGE = re.compile(_MONTH_RE + r"\s*-\s*" + _MONTH_RE + r"(?:\s+(\d{4}))?", re.I)
RE_DECADE = re.compile(r"(перв|втор|трет)\w*\s+декад\w*\s+" + _MONTH_RE + r"(?:\s+(\d{4}))?", re.I)
RE_PART = re.compile(r"(начал|середин|конц|конец|окончани)\w*\s+" + _MONTH_RE + r"(?:\s+(\d{4}))?", re.I)
RE_MONTH = re.compile(r"(?<![\w])" + _MONTH_RE + r"(?:\s+(\d{4}))?(?![\w])", re.I)
RE_SEASON = re.compile(
    r"(начал\w*\s+|середин\w*\s+|конц\w*\s+|конец\s+)?(зим|весн|лет|осен)\w*(?:\s+(\d{4})(?:\s*/\s*(\d{2,4}))?)?",
    re.I,
)
RE_YEAR = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")


def _year_or(y: str | None, hint: int | None) -> tuple[int | None, bool]:
    if y:
        return _year4(y), True
    return hint, False


def parse_date(raw: str | None, year_hint: int | None = None) -> DateResult:
    """Разобрать дату. Пустая строка/None → пустой результат (не ambiguous)."""
    if raw is None or not str(raw).strip():
        return DateResult()
    s = _clean(str(raw))
    res = _parse(s, year_hint)
    if res is None:
        return DateResult(ambiguous=True, note=f"дата «{raw}» не распознана")
    if res.date_from is None:
        res.ambiguous = True
        if not res.note:
            res.note = "год даты неизвестен" if res.year is None else f"дата «{raw}» не распознана"
    # Остаток текста, кроме служебных слов, означает, что дата понята не полностью.
    return res


def _finish(r: DateResult, s: str, span: tuple[int, int]) -> DateResult:
    rest = (s[: span[0]] + " " + s[span[1]:]).lower()
    rest = re.sub(r"\b(г|гг|год[а-я]*|с|по|в|во|до|около|примерно|ок|числ[а-я]*)\b\.?", " ", rest)
    rest = re.sub(r"[\s.,;:()\-]+", "", rest)
    if re.search(r"\d", rest) or len(rest) > 12:
        r.ambiguous = True
    if r.date_from and r.date_to and r.date_from > r.date_to:
        r.ambiguous = True
    return r


def _parse(s: str, hint: int | None) -> DateResult | None:
    # --- интервалы
    m = RE_RANGE_NUM_FULL.search(s)
    if m:
        d1, m1, y1, d2, m2, y2 = m.groups()
        yb, ft = _year_or(y2 or y1, hint)
        ya = _year4(y1) if y1 else (yb if yb is None or int(m1) <= int(m2) else yb - 1)
        if yb is None:
            return DateResult(precision="range", ambiguous=True)
        r = DateResult(_iso(ya, int(m1), int(d1)), _iso(yb, int(m2), int(d2)), "range", yb, ft)
        return _finish(r, s, m.span())
    m = RE_RANGE_NUM_SAME.search(s)
    if m:
        d1, d2, mo, y = m.groups()
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="range", ambiguous=True)
        r = DateResult(_iso(yy, int(mo), int(d1)), _iso(yy, int(mo), int(d2)), "range", yy, ft)
        return _finish(r, s, m.span())
    m = RE_RANGE_WORD_FULL.search(s)
    if m:
        d1, mw1, d2, mw2, y = m.groups()
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="range", ambiguous=True)
        r = DateResult(_iso(yy, month_num(mw1), int(d1)), _iso(yy, month_num(mw2), int(d2)), "range", yy, ft)
        return _finish(r, s, m.span())
    m = RE_RANGE_WORD_SAME.search(s)
    if m:
        d1, d2, mw, y = m.groups()
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="range", ambiguous=True)
        mo = month_num(mw)
        r = DateResult(_iso(yy, mo, int(d1)), _iso(yy, mo, int(d2)), "range", yy, ft)
        return _finish(r, s, m.span())
    m = RE_MONTH_RANGE.search(s)
    if m and not RE_DAY_WORD.search(s):
        mw1, mw2, y = m.groups()
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="range", ambiguous=True)
        m1, m2 = month_num(mw1), month_num(mw2)
        r = DateResult(_iso(yy, m1, 1), _iso(yy, m2, _last_day(yy, m2)), "range", yy, ft)
        return _finish(r, s, m.span())
    # --- день
    days = list(RE_DAY_NUM.finditer(s))
    wdays = list(RE_DAY_WORD.finditer(s))
    if days or wdays:
        cands = []
        for m in days:
            d, mo, y = m.groups()
            cands.append((m.span(), int(d), int(mo), y))
        for m in wdays:
            d, mw, y = m.groups()
            cands.append((m.span(), int(d), month_num(mw), y))
        cands.sort()
        span, d, mo, y = cands[0]
        if not (1 <= mo <= 12):
            return None
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="day", ambiguous=True)
        iso = _iso(yy, mo, d)
        if iso is None:
            return None
        r = DateResult(iso, iso, "day", yy, ft)
        if len(cands) > 1:
            # несколько дат в одной строке — берём интервал от первой до последней
            isos = []
            for _, d2, mo2, y2 in cands:
                y3 = _year4(y2) if y2 else yy
                v = _iso(y3, mo2, d2) if mo2 else None
                if v:
                    isos.append(v)
            if isos and min(isos) != max(isos):
                r.date_from, r.date_to, r.precision = min(isos), max(isos), "range"
            r.ambiguous = True
            r.note = "в строке несколько дат"
            return r
        return _finish(r, s, span)
    # --- декада
    m = RE_DECADE.search(s)
    if m:
        k, mw, y = m.groups()
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="decade", ambiguous=True)
        mo = month_num(mw)
        idx = {"перв": 0, "втор": 1, "трет": 2}[k.lower()[:4]]
        a, b = [(1, 10), (11, 20), (21, _last_day(yy, mo))][idx]
        return _finish(DateResult(_iso(yy, mo, a), _iso(yy, mo, b), "decade", yy, ft), s, m.span())
    m = RE_PART.search(s)
    if m:
        k, mw, y = m.groups()
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="decade", ambiguous=True)
        mo = month_num(mw)
        k = k.lower()
        a, b = (1, 10) if k.startswith("начал") else (11, 20) if k.startswith("середин") else (21, _last_day(yy, mo))
        return _finish(DateResult(_iso(yy, mo, a), _iso(yy, mo, b), "decade", yy, ft), s, m.span())
    # --- месяц
    m = RE_MONTH.search(s)
    if m:
        mw, y = m.groups()
        yy, ft = _year_or(y, hint)
        if yy is None:
            return DateResult(precision="month", ambiguous=True)
        mo = month_num(mw)
        return _finish(DateResult(_iso(yy, mo, 1), _iso(yy, mo, _last_day(yy, mo)), "month", yy, ft), s, m.span())
    # --- сезон
    m = RE_SEASON.search(s)
    if m and re.match(r"(зим|весн|лет|осен)", m.group(2).lower()) and _is_season_word(s, m):
        part, stem, y1, y2 = m.groups()
        stem = stem.lower()
        m_from, m_to, shift = SEASONS[stem]
        amb = False
        if y1 and y2:  # «зимой 1995/96» — сезон с декабря первого года
            ya = _year4(y1)
            ys = ya + 1
            ft = True
        elif y1:
            ys, ft = _year4(y1), True
        else:
            ys, ft = hint, False
            amb = stem == "зим"  # без года непонятно, какая зима
        if ys is None:
            return DateResult(precision="season", ambiguous=True)
        ya = ys + shift
        d_from = _iso(ya, m_from, 1)
        d_to = _iso(ys, m_to, _last_day(ys, m_to))
        if part:
            p = part.lower()
            months = _season_months(ya, ys, m_from, m_to)
            if p.startswith("начал"):
                (y_a, mo_a) = months[0]
                d_from, d_to = _iso(y_a, mo_a, 1), _iso(y_a, mo_a, _last_day(y_a, mo_a))
            elif p.startswith("кон"):
                (y_b, mo_b) = months[-1]
                d_from, d_to = _iso(y_b, mo_b, 1), _iso(y_b, mo_b, _last_day(y_b, mo_b))
            else:
                (y_c, mo_c) = months[1]
                d_from, d_to = _iso(y_c, mo_c, 1), _iso(y_c, mo_c, _last_day(y_c, mo_c))
        r = DateResult(d_from, d_to, "season", ys, ft, ambiguous=amb)
        return _finish(r, s, m.span())
    # --- год
    m = RE_YEAR.search(s)
    if m:
        y = int(m.group(1))
        return _finish(DateResult(_iso(y, 1, 1), _iso(y, 12, 31), "year", y, True), s, m.span())
    return None


def _is_season_word(s: str, m: re.Match) -> bool:
    w = s[m.start(2):].split(" ")[0].lower()
    return bool(re.match(r"(зим(а|ой|ы|е|у|ний|него|нем|нюю|ние)?|весн(а|ой|ы|е|у|ний|него|нем)?|"
                         r"лет(о|ом|а|е|ний|него|нем)|осен(ь|ью|и|ний|него|нем))\b", w))


def _season_months(ya: int, ys: int, m_from: int, m_to: int) -> list[tuple[int, int]]:
    out, y, mo = [], ya, m_from
    while True:
        out.append((y, mo))
        if (y, mo) == (ys, m_to):
            break
        mo += 1
        if mo == 13:
            mo, y = 1, y + 1
    return out


def date_in_text(date_raw: str | None, text: str | None) -> bool:
    """Есть ли дата (как написана) в тексте — для флага date_not_in_quote.

    Сравнение по нормализованной строке, а при неудаче — по набору числовых компонентов
    и названий месяцев (кавычки цитаты могут отличаться пробелами/переносами).
    """
    if not date_raw:
        return True
    from .text import fold

    a, b = fold(date_raw), fold(text)
    if a and a in b:
        return True
    nums = re.findall(r"\d+", a)
    words = [w for w in re.findall(r"[а-я]+", a) if month_num(w) or w[:3] in ("зим", "вес", "лет", "осе", "дек")]
    if not nums and not words:
        return False
    b_nums = set(re.findall(r"\d+", b))
    norm = lambda x: x.lstrip("0") or "0"  # noqa: E731
    b_nums_n = {norm(x) for x in b_nums}
    ok_nums = all(norm(n) in b_nums_n for n in nums)
    ok_words = all(w[:4] in b for w in words)
    return ok_nums and ok_words
