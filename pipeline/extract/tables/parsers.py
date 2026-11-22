"""Детерминированные парсеры таблиц известных типов.

| Тип | Как узнаём | Что даёт |
|---|---|---|
| ``year_columns`` (табл. 7.10 «Сравнительная хронология зацветания») | столбец «Вид» + столбцы-годы | запись на каждый год: obs_year = год столбца, фенофаза из заголовка («зацветание» = фаза 5) |
| ``calendar`` (раздел 6 «Календарь природы») | первый столбец «Основные сезонные процессы…» | дата наступления явления; многолетние средние/крайние даты — в note |
| ``phenomatrix`` (табл. 7.11–7.12 «вид × номер фенофазы») | столбец «Вид…» + ≥5 столбцов-кодов 1…16, 10а, 10б | запись на каждую фазу; местообитание — строка-группа, топоним — из заголовка |
| ``counts`` (учёты: «вид × число/маршрут», табл. 8.87) | столбец «Вид…» + числовые ячейки | запись на каждую числовую ячейку: indicator = заголовок столбца |

Каждый парсер возвращает список ``Draft``; цитата — минимальный дословный фрагмент строки,
содержащий название и значение.
"""
from __future__ import annotations

import re

from pipeline.normalize.persons import INITS, SURNAME
from pipeline.normalize.places import find_places_in_text
from pipeline.normalize.text import parse_number

from .common import (
    DATE_RE, EMPTY_VALUES, YEAR_RE, Cell, Draft, TableContext, caption_title, caption_year, find_key,
    span_quote, strip_figure,
)

PHENO_CODE = re.compile(r"^(1[0-6][аб]?|[1-9])$")
TOTAL_RE = re.compile(r"^(всего|итого|сумма|среднее)", re.I)
ENV_RE = re.compile(
    r"температур|снег|снеж|ледостав|лед|льд|вскрыти|заморозк|осадк|покров|ветер|гроз|дожд|туман|вод[аы]|"
    r"паводок|половод|оттепел|прохлад|охлаждени|санный путь|проталин|ледоход|замерзани|радуга|иней|роса", re.I
)
ICE_EVENT_RE = re.compile(r"ледостав|вскрыти|ледоход|замерзани|проталин|появлени\w* воды", re.I)


def detect(cells: list[Cell]) -> str | None:
    """Тип таблицы по ключам строки."""
    keys = [c.key for c in cells]
    k0 = keys[0].lower()
    species = find_key(cells, "вид", "виды", "название", "наименование")
    if k0.startswith("основные сезонные процессы") or k0.startswith("сезонные процессы"):
        return "calendar"
    if species is not None:
        codes = [k for k in keys if PHENO_CODE.match(k)]
        if len(codes) >= 5:
            return "phenomatrix"
        years = [k for k in keys if re.fullmatch(r"(19|20)\d{2}", k)]
        if years:
            return "year_columns"
        nums = [c for c in cells if c is not species and not c.key.startswith("№") and _numeric(c.value)]
        if nums:
            return "counts"
    return None


def _numeric(v: str) -> bool:
    v = v.strip()
    if v in EMPTY_VALUES or DATE_RE.fullmatch(v):
        return False
    return bool(re.fullmatch(r"[~<>≈]?\s*\d+(?:[.,]\d+)?(?:\s*\(\s*\d+\s*\))?(?:\s*/\s*\d+(?:[.,]\d+)?)?\s*%?", v))


DATE_CAPTION_RE = re.compile(r"зацвет|цветени|дат[аы]|сроки|фенолог|хронолог|прил[её]т|отл[её]т|наступлени|созревани")


def _valid_ddmm(v: str) -> bool:
    m = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?", v)
    return bool(m) and 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12


def _quote(text: str, a: Cell, b: Cell) -> tuple[str, tuple[int, int]]:
    lo, hi = min(a.vstart, b.vstart), max(a.end, b.end)
    return span_quote(text, lo, hi)


# --------------------------------------------------------------------------- 7.10

def parse_year_columns(text: str, cells: list[Cell], ctx: TableContext) -> list[Draft]:
    sp = find_key(cells, "вид", "виды", "название", "наименование")
    species, fig = strip_figure(sp.value)
    if not species:
        return []
    cap = (ctx.caption or "").lower()
    if "зацвет" in cap or "цветени" in cap:
        event, pheno = "зацветание", "зацветание"
    elif "плодонош" in cap or "созреван" in cap:
        event, pheno = "созревание плодов", "созревание плодов"
    else:
        event, pheno = caption_title(ctx.caption) or "дата явления", None
    label = ctx.label
    # значения — даты, только если таблица про сроки явлений; иначе это числа по годам (плотность, учёт)
    date_table = bool(DATE_CAPTION_RE.search(cap)) or not ctx.caption
    out: list[Draft] = []
    for c in cells:
        if not re.fullmatch(r"(19|20)\d{2}", c.key):
            continue
        v = c.value.strip()
        if v in EMPTY_VALUES:
            continue
        year = int(c.key)
        notes = [f"Год наблюдения взят из заголовка столбца {year}."]
        if not date_table or (not _valid_ddmm(v) and _numeric(v)):
            q, span = _quote(text, sp, c)
            title = caption_title(ctx.caption)
            f = {
                "record_type": "observation", "object_type": "taxon", "object_raw": species, "obs_year": year,
                "event_raw": "учёт", "indicator": (title[:150] if title else f"значение за {year}"),
                "value_raw": v, "value_num": parse_number(v),
            }
            if ctx.caption:
                notes.insert(0, f"{label or 'Таблица'} «{title}», столбец {year}; единица в заголовке не указана.")
            out.append(Draft(f, q, span, "table:year_columns", False, notes))
            continue
        uncertain = False
        m = DATE_RE.search(v)
        if not m:
            uncertain = True
            notes.append(f"значение ячейки «{v}» не похоже на дату")
            date_raw = v
        else:
            date_raw = m.group(0)
            rest = (v[: m.start()] + v[m.end():]).strip()
            if rest:
                second = DATE_RE.search(rest)
                if second and "(" in rest:
                    notes.append(f"в скобках — дата вторичного цветения: {second.group(0)}")
                else:
                    notes.append(f"в ячейке также: «{rest}»")
        q, span = _quote(text, sp, c)
        f = {
            "record_type": "observation", "object_type": "taxon", "object_raw": species,
            "date_raw": date_raw, "obs_year": year,
            "event_raw": f"{event} ({label})" if label else event, "phenophase_raw": pheno,
        }
        if ctx.caption:
            notes.insert(0, f"{label or 'Таблица'} «{caption_title(ctx.caption)}», столбец {year}.")
        out.append(Draft(f, q, span, "table:year_columns", uncertain, notes))
    return out


# --------------------------------------------------------------------------- календарь природы

def parse_calendar(text: str, cells: list[Cell], ctx: TableContext) -> list[Draft]:
    obj = cells[0]
    name = obj.value.strip()
    if not name:
        return []
    date_cell = find_key(cells, "дата наступления", "дата") or (cells[1] if len(cells) > 1 else None)
    if date_cell is None or date_cell is obj or date_cell.value.strip() in EMPTY_VALUES:
        return []
    v = date_cell.value.strip()
    m = DATE_RE.search(v)
    year_hint = None
    ym = YEAR_RE.search(date_cell.key)
    if ym:
        year_hint = int(ym.group(1))
    notes = []
    extra = [f"{c.key.lower()}: {c.value}" for c in cells if c is not obj and c is not date_cell and c.value]
    if ctx.group:
        notes.append(f"Календарь природы, сезон «{ctx.group}».")
    else:
        notes.append("Календарь природы.")
    if extra:
        notes.append("; ".join(extra) + ".")
    is_env = bool(ENV_RE.search(name))
    places = find_places_in_text(name)
    q, span = _quote(text, obj, date_cell)
    f = {
        "record_type": "env_measurement" if is_env else "observation",
        "object_type": ("phenomenon" if ICE_EVENT_RE.search(name) else "env_indicator") if is_env else "taxon",
        "object_raw": name, "date_raw": m.group(0) if m else v, "event_raw": name,
        "indicator": f"дата: {name[0].lower() + name[1:]}" if is_env else None,
        "toponym_raw": places[0][1] if places else None,
        "_year_hint": year_hint,
    }
    return [Draft(f, q, span, "table:calendar", not bool(m), notes, observers_from_section=False)]


# --------------------------------------------------------------------------- вид × фенофаза

def parse_phenomatrix(text: str, cells: list[Cell], ctx: TableContext, phase_names: dict[str, str]) -> list[Draft]:
    sp = cells[0]
    species = sp.value.strip()
    if not species or re.match(r"^[a-zа-яё)]", species):
        return []  # хвост названия с предыдущей страницы («северная)»)
    year = caption_year(ctx.caption)
    places = find_places_in_text(ctx.caption or "")
    out = []
    for c in cells[1:]:
        if not PHENO_CODE.match(c.key):
            continue
        v = c.value.strip()
        if v in EMPTY_VALUES:
            continue
        m = DATE_RE.search(v)
        if not m:
            continue
        code = c.key
        name = phase_names.get(code)
        notes = []
        if ctx.caption:
            notes.append(f"{ctx.label or 'Таблица'} «{caption_title(ctx.caption)}».")
        if ctx.group:
            notes.append("Местообитание — строка-группа таблицы.")
        if places:
            notes.append("Топоним — из заголовка таблицы.")
        if year is None:
            notes.append("Год — по году книги.")
        q, span = _quote(text, sp, c)
        f = {
            "record_type": "observation", "object_type": "taxon", "object_raw": species,
            "date_raw": m.group(0), "obs_year": year,
            "event_raw": f"фаза {code} - {name}" if name else f"фаза {code}",
            "phenophase_raw": code, "_phenophase_code": code,
            "habitat_raw": ctx.group, "toponym_raw": places[0][1] if places else None,
        }
        out.append(Draft(f, q, span, "table:phenomatrix", False, notes))
    return out


# --------------------------------------------------------------------------- учёты

_OBS_IN_CAPTION = re.compile(rf"(?:{SURNAME}\s+{INITS}|{INITS}\s*{SURNAME})")


def parse_counts(text: str, cells: list[Cell], ctx: TableContext) -> list[Draft]:
    sp = find_key(cells, "вид", "виды", "название", "наименование")
    species, _ = strip_figure(sp.value)
    if not species or TOTAL_RE.match(species):
        return []
    generic_keys = sum(c.key.startswith("колонка") for c in cells)
    uncertain = generic_keys > 0
    year = caption_year(ctx.caption)
    obs = _OBS_IN_CAPTION.findall(ctx.caption or "")
    out = []
    for c in cells:
        if c is sp or c.key.startswith("№") or not _numeric(c.value) or TOTAL_RE.match(c.key):
            continue
        key_l = c.key.lower()
        unit = "особей" if "особ" in key_l else "следов/10 км" if "10 км" in key_l else "шт." if "шт" in key_l else None
        places = find_places_in_text(c.key)
        notes = []
        if ctx.caption:
            notes.append(f"{ctx.label or 'Таблица'} «{caption_title(ctx.caption)}», столбец «{c.key}».")
        if uncertain:
            notes.append("Заголовки части столбцов не распознаны.")
        q, span = _quote(text, sp, c)
        f = {
            "record_type": "observation", "object_type": "taxon", "object_raw": species,
            "obs_year": year, "event_raw": "учёт",
            "indicator": c.key, "value_raw": c.value.strip(), "value_num": parse_number(c.value),
            "unit": unit, "toponym_raw": c.key if places else None,
            "habitat_raw": c.key if re.search(r"маршрут|линия|биотоп", key_l) else None,
            "observers_raw": ", ".join(obs) if obs else None,
        }
        out.append(Draft(f, q, span, "table:counts", uncertain, notes))
    return out
