"""event_raw / phenophase_raw → phenophase_id по ключевым словам и шкале fixtures/phenophases.json.

Шкала растений — коды 1–16, 10а/10б (кириллица) из легенды книги 2020; «зацветание» = фаза 5
«начало цветения» (Решения, п. 6). Животные — коды arrival, first_song, first_seen_spring, breeding,
young, moult_summer, moult_winter, departure.

Явный номер («фаза 5», «фенофаза 10б», «5 (BBCH 60)») имеет приоритет над ключевыми словами.
Шкала выбирается по группе таксона (растения/животные); если группа неизвестна — по словам.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .dicts import load
from .text import fold

PLANT_GROUPS = {"сосудистые растения", "мхи", "лишайники", "грибы"}

# (регулярное выражение по fold-строке, код). Порядок важен: более специфичные — раньше.
PLANT_RULES: list[tuple[str, str]] = [
    (r"окончани\w* листопад|полн\w* отмирани", "16"),
    (r"массов\w* (опадени|листопад)|массов\w* отмирани", "15"),
    (r"(начал\w* )?листопад|начал\w* отмирани", "14"),
    (r"массов\w* (изменени\w* окраск|пожелтени|расцвечивани)", "13"),
    (r"изменени\w* окраск|пожелтени|осенн\w* окраск|расцвечивани", "12"),
    (r"массов\w* (созревани|поспевани|рассеивани)|поспевани", "11"),
    (r"созревани\w* плод|зрел\w* плод|рассеивани\w* семян|созревани\w* (ягод|семян)", "10б"),
    (r"незрел\w* плод|образовани\w* плод|завязывани\w* плод|появлени\w* плод", "10а"),
    (r"полн\w* отцветани|конец цветения|окончани\w* цветени", "9"),
    (r"массов\w* отцветани", "8"),
    (r"отцветани", "7"),
    (r"массов\w* цветени|массов\w* зацветани", "6"),
    (r"зацвет|начал\w* цветени|первые цвет|цветени", "5"),
    (r"бутониз|бутон", "4"),
    (r"полн\w* лист|облиствени", "3"),
    (r"зеленени|развертывани\w* лист|распускани\w* лист|появлени\w* лист", "2"),
    (r"распускани\w* поч|развертывани\w* поч|набухани\w* поч|начал\w* рост|начал\w* вегетаци|сокодвижени|пылени", "1"),
]
ANIMAL_RULES: list[tuple[str, str]] = [
    (r"последн\w* встреч|отлет|откочевк|осенн\w* пролет|залегани\w* в (берлог|спячк)", "departure"),
    (r"летн\w* (мех|наряд|оперени)|линьк\w* на лет", "moult_summer"),
    (r"зимн\w* (мех|наряд|оперени)|побелени|линьк\w* на зим", "moult_winter"),
    (r"выводок|птенц|детеныш|молодняк|новорожд|лосенок|медвежат|слеток|сеголет", "young"),
    (r"гнезд|кладк|насиживани|гон\b|спаривани|токовани|брачн|нерест", "breeding"),
    (r"песн|пени|кукован|барабанн\w* дроб|урчани|кваканье|токует", "first_song"),
    (r"прилет|пролет|появлени\w* (первых )?(птиц|особ)|прибыти", "arrival"),
    (r"выход из (берлог|спячк|нор)|пробуждени|первая встреча|первые следы|первое появлени|появлени", "first_seen_spring"),
]
_EXPLICIT_RE = re.compile(r"(?:фенофаз\w*|фаз\w*)\s*№?\s*(1[0-6][аб]?|[1-9])(?![\d])|^\s*(1[0-6][аб]?|[1-9])\s*\(bbch", re.I)


@lru_cache(maxsize=1)
def _codes() -> dict[tuple[str, str], int]:
    return {(p["applies_to"], p["code"]): p["id"] for p in load().phenophases}


def _id(applies: str, code: str) -> int | None:
    if re.fullmatch(r"1[0-6][ab]", code):
        code = code.replace("a", "а").replace("b", "б")  # латинские a/b → кириллица
    return _codes().get((applies, code))


def explicit_code(text: str | None) -> str | None:
    m = _EXPLICIT_RE.search(fold(text))
    if not m:
        return None
    code = m.group(1) or m.group(2)
    return code.replace("a", "а").replace("b", "б")


def match_phenophase(*texts: str | None, group: str | None = None) -> tuple[int | None, str | None]:
    """→ (phenophase_id, сработавший фрагмент). Пустые тексты пропускаются."""
    text = " ; ".join(fold(t) for t in texts if t)
    if not text:
        return None, None
    code = explicit_code(text)
    if code:
        pid = _id("plants", code)
        if pid:
            return pid, f"фаза {code}"
    if group is not None:
        order = ["plants"] if group in PLANT_GROUPS else ["animals"]
    else:
        order = ["plants", "animals"]
    for applies in order:
        rules = PLANT_RULES if applies == "plants" else ANIMAL_RULES
        for rx, code in rules:
            m = re.search(rx, text)
            if m:
                pid = _id(applies, code)
                if pid:
                    return pid, m.group(0)
    return None, None


def code_to_id(code: str, applies: str = "plants") -> int | None:
    return _id(applies, code.strip())
