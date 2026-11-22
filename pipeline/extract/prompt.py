"""Промпт LLM-извлечения: системная инструкция, few-shot из фикстур, контекст и схема ответа.

Изменение текста промпта или схемы → увеличить PROMPT_VERSION (входит в ключ кэша и в
extractor_version записей).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline.normalize.dicts import FIXTURES

PROMPT_VERSION = "p1"


class LLMRecord(BaseModel):
    """Запись, которую возвращает модель (подмножество Observation + номер фрагмента)."""

    model_config = ConfigDict(extra="ignore")

    chunk_ref: int = Field(1, description="Номер фрагмента [N], из которого взята цитата")
    record_type: Literal["observation", "env_measurement", "publication"]
    object_type: Literal["taxon", "phenomenon", "env_indicator", "publication"]
    object_raw: str = Field(..., min_length=1, description="Объект как в тексте")
    quote: str = Field(..., min_length=3, description="Дословная минимальная цитата из фрагмента")
    date_raw: str | None = None
    obs_year: int | None = None
    toponym_raw: str | None = None
    coords_raw: str | None = None
    habitat_raw: str | None = None
    event_raw: str | None = None
    phenophase_raw: str | None = None
    indicator: str | None = None
    value_raw: str | None = None
    value_num: float | None = None
    unit: str | None = None
    observers_raw: str | None = None
    pub_authors: str | None = None
    pub_year: int | None = None
    pub_title: str | None = None
    pub_venue: str | None = None
    note: str | None = None


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    records: list[LLMRecord] = Field(default_factory=list)


def response_json_schema() -> dict:
    """JSON Schema ответа для response_format=json_schema (без $defs — их не все API понимают)."""
    nullable = lambda t: {"type": [t, "null"]}  # noqa: E731
    props = {
        "chunk_ref": {"type": "integer"},
        "record_type": {"type": "string", "enum": ["observation", "env_measurement", "publication"]},
        "object_type": {"type": "string", "enum": ["taxon", "phenomenon", "env_indicator", "publication"]},
        "object_raw": {"type": "string"},
        "quote": {"type": "string"},
    }
    for k in ("date_raw", "toponym_raw", "coords_raw", "habitat_raw", "event_raw", "phenophase_raw", "indicator",
              "value_raw", "unit", "observers_raw", "pub_authors", "pub_title", "pub_venue", "note"):
        props[k] = nullable("string")
    for k in ("obs_year", "pub_year"):
        props[k] = nullable("integer")
    props["value_num"] = nullable("number")
    return {
        "type": "object",
        "properties": {"records": {"type": "array", "items": {
            "type": "object", "properties": props,
            "required": ["chunk_ref", "record_type", "object_type", "object_raw", "quote"],
            "additionalProperties": False,
        }}},
        "required": ["records"],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = """Ты извлекаешь научные наблюдения из «Летописи природы» заповедника «Пасвик» (Мурманская область).
На входе — контекст (книга, раздел, авторы раздела, страница, предыдущий абзац) и пронумерованные фрагменты текста [1], [2], …
Верни JSON {"records": [...]} — массив записей; если наблюдений нет, верни {"records": []}.

Что считать записью:
- observation — наблюдение вида или явления природы: встреча, следы, учёт, фенологическое событие (прилёт, зацветание, выход из берлоги), находка;
- env_measurement — погода, температура, снег, лёд, вода (уровень, вскрытие, ледостав);
- publication — публикация (в библиографии или разделе «Научные исследования»): pub_authors, pub_year, pub_title, pub_venue.
Одна запись = один объект × одна дата (или один показатель). Если в предложении несколько видов или дат — несколько записей.
Методику, общие рассуждения, литературные ссылки внутри текста и планы работ записями не считай.

Правила (обязательны):
1. quote — ДОСЛОВНЫЙ фрагмент входного текста (копируй символы как есть, включая переносы и сокращения), минимальный, но содержащий все значения записи (объект, дату, место, число). Не исправляй опечатки и не перефразируй.
2. Значения, которых нет в тексте, = null. Ничего не додумывай: не подставляй вид по смыслу, не вычисляй даты, не переводи названия.
3. date_raw — дата дословно, как в тексте («12.05», «в конце мая», «с 30 по 31 марта»); нет даты — null.
4. obs_year — год наблюдения: из самой даты или текста; если в тексте года нет — возьми obs_year_hint из контекста.
5. value_raw — число как в тексте («3,58», «Четыре», «32/22»), value_num — то же числом (3.58, 4, 32), unit — единица отдельно («особей», «следов/10 км», «см», «°С»), indicator — что измеряли.
6. observers_raw — наблюдатели, только если они названы в тексте («(Бычков Ю.М.)», «Фото: Валова Е.В.»). Если не названы — null (авторов раздела подставит программа).
7. object_raw — объект как в тексте («лосиха с лосенком», «Первый ледостав на р. Паз»); toponym_raw — место как в тексте («у устья Лауккуйоки»); habitat_raw — местообитание («сосняк бруснично-черничный»); event_raw — характер явления («переплывали реку», «найдена берлога»); phenophase_raw — фенофаза словами из текста («зацветание», «прилёт»).
8. chunk_ref — номер фрагмента [N], из которого взята цитата. Цитата не должна пересекать границы фрагментов.
Отвечай только JSON без пояснений."""


@dataclass
class PromptContext:
    book_title: str
    book_year: int | None
    section_code: str | None = None
    section_title: str | None = None
    section_authors: str | None = None
    printed_page: str | None = None
    pdf_page: int | None = None
    prev_text: str | None = None
    table_caption: str | None = None
    table_group: str | None = None
    obs_year_hint: int | None = None
    chunks: list[str] = field(default_factory=list)


def render_user(ctx: PromptContext) -> str:
    lines = [f"Книга: {ctx.book_title}" + (f" (год {ctx.book_year})" if ctx.book_year else "")]
    if ctx.section_title:
        lines.append(f"Раздел: {(ctx.section_code + ' ') if ctx.section_code else ''}{ctx.section_title}")
    lines.append(f"Авторы раздела: {ctx.section_authors or 'не указаны'}")
    if ctx.printed_page or ctx.pdf_page:
        lines.append(f"Страница: {ctx.printed_page or ctx.pdf_page}")
    lines.append(f"obs_year_hint: {ctx.obs_year_hint or ctx.book_year}")
    if ctx.table_caption:
        lines.append(f"Таблица: {ctx.table_caption}")
    if ctx.table_group:
        lines.append(f"Строка-группа таблицы: {ctx.table_group}")
    if ctx.prev_text:
        lines.append(f"Предыдущий абзац (только для связности, из него записи не извлекать): «{ctx.prev_text}»")
    lines.append("")
    lines.append("Фрагменты:")
    for i, t in enumerate(ctx.chunks, 1):
        lines.append(f"[{i}] {t}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- few-shot из фикстур

_FEW_SHOT_IDS = [(1, 2, 3), (26,), (10,)]  # растение-фенология, животное-учёт, погода
_REC_FIELDS = ["record_type", "object_type", "object_raw", "quote", "date_raw", "obs_year", "toponym_raw",
               "coords_raw", "habitat_raw", "event_raw", "phenophase_raw", "indicator", "value_raw", "value_num",
               "unit", "observers_raw", "pub_authors", "pub_year", "pub_title", "pub_venue"]


@lru_cache(maxsize=1)
def few_shot_messages() -> list[dict]:
    """3 пары user/assistant, собранные из contracts/fixtures (фрагмент вокруг цитаты + записи)."""
    obs = {}
    p = FIXTURES / "observations.jsonl"
    if not p.exists():
        return []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            o = json.loads(line)
            obs[o["id"]] = o
    chunks = {}
    for line in (FIXTURES / "chunks.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            chunks[c["id"]] = c
    sections = {s["id"]: s for s in json.loads((FIXTURES / "sections.json").read_text(encoding="utf-8"))}
    msgs: list[dict] = []
    for group in _FEW_SHOT_IDS:
        recs = [obs[i] for i in group if i in obs]
        if not recs:
            continue
        c = chunks.get(recs[0]["chunk_id"])
        if not c or not recs[0].get("quote_span"):
            continue
        a, b = recs[0]["quote_span"]
        text = c["text"]
        # фрагмент: строки, в которых лежит цитата (без соседних наблюдений — иначе пример учил бы их пропускать)
        lo = text.rfind("\n", 0, a)
        hi = text.find("\n", b)
        frag = text[lo + 1 if lo >= 0 else 0: hi if hi > 0 else len(text)].strip()
        sec = sections.get(recs[0].get("section_id")) or {}
        ctx = PromptContext(
            book_title="Летопись природы заповедника «Пасвик». Книга 27 (2020 год)", book_year=2020,
            section_code=sec.get("code"), section_title=sec.get("title"),
            section_authors=None, printed_page=(recs[0].get("locator") or {}).get("printed_page"),
            obs_year_hint=2020, chunks=[frag],
        )
        out = []
        for r in recs:
            d = {k: r.get(k) for k in _REC_FIELDS}
            d["chunk_ref"] = 1
            if r.get("observers_raw") and r["observers_raw"] not in frag:
                d["observers_raw"] = None  # авторы раздела подставляются кодом, не моделью
            if d["quote"] not in frag:
                continue
            out.append(d)
        if out:
            msgs.append({"role": "user", "content": render_user(ctx)})
            msgs.append({"role": "assistant", "content": json.dumps({"records": out}, ensure_ascii=False)})
    return msgs


def build_messages(ctx: PromptContext) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, *few_shot_messages(),
            {"role": "user", "content": render_user(ctx)}]
