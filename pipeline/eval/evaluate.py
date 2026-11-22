"""Оценка качества по эталону: gold CSV (формат gold/template.csv) ↔ data/observations/*.jsonl → data/quality.json.

    python -m pipeline.eval.evaluate --gold gold/sanity_2020.csv [--scope object|page] [--out data/quality.json]

1. **Сопоставление записей.** Для каждой пары «эталон × извлечённая» в одном документе и на
   соседних страницах (|Δ pdf_page| ≤ 1) считается сходство: объект (русское/латинское название
   эталона против object_raw и названий таксона из справочника, rapidfuzz token_set_ratio;
   совпадение taxon_id — 100) + дата + год + точная страница. Пары выбираются жадно по убыванию
   сходства, порог по объекту — 70.
2. **Область оценки** (какие извлечённые записи могут считаться лишними):
   - ``page`` — эталон размечает страницы целиком: лишняя — любая несопоставленная запись на этих страницах;
   - ``object`` — эталон размечает выбранные объекты (sanity-эталон): лишняя — несопоставленная
     запись на этих страницах об объекте, который есть в эталоне этой страницы.
3. **Поля:** дата, год, вид (рус.), вид (лат.), топоним, местообитание, численные, фенофаза,
   наблюдатель, страница. Для сопоставленной пары: эталон и извлечение непусты и совпали → TP;
   извлечение непусто и не совпало → FP (и FN, если эталон непуст); эталон непуст, извлечение пусто → FN.
   Несопоставленная эталонная запись → FN по каждому непустому полю; лишняя запись → FP по каждому
   непустому полю. precision = TP/(TP+FP), recall = TP/(TP+FN).
4. Примеры ошибок (missed / spurious / wrong_value) и список неподдерживаемых страниц
   (нет текстового слоя и нет OCR) — в тот же quality.json (схема ``$defs/Quality``).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from rapidfuzz import fuzz

from pipeline.extract.extractor import EXTRACTOR_VERSION
from pipeline.normalize.dates import parse_date
from pipeline.normalize.dicts import ROOT, load
from pipeline.normalize.persons import match_people
from pipeline.normalize.phenophases import match_phenophase
from pipeline.normalize.places import match_place
from pipeline.normalize.taxa import _LAT_BINOM_RE, clean_name_keep_case, match_taxon
from pipeline.normalize.text import fold, numbers

H = {
    "date": "Дата наблюдения", "year": "Год наблюдения", "observer": "ФИО наблюдателя",
    "species_ru": "Наименование вида (русский)", "species_lat": "Наименование вида (латынь)",
    "toponym": "Место регистрации (топоним)", "coords": "Координаты встречи", "habitat": "Местообитание",
    "event": "Характер явления, наблюдения", "numeric": "Численные показатели",
    "phenophase": "Фенологическая фаза", "page": "Ссылка на страницу/-цы в документе", "note": "Примечание",
    "pdf_page": "PDF-страница", "document": "Документ", "quote": "Цитата",
}
FIELDS = ["date", "year", "species_ru", "species_lat", "toponym", "habitat", "numeric", "phenophase", "observer", "page"]
EMPTY = {"", "-", "—", "нет", "не фиксировалась", "не указано", "null"}


def _v(s) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    return None if fold(s) in EMPTY else s


def read_gold(path: Path) -> list[dict]:
    docs = load().documents
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    out = []
    for i, r in enumerate(rows, 2):
        g = {k: _v(r.get(h)) for k, h in H.items()}
        d = g["document"] or ""
        # «Документ» — id документа или имя файла (номер книги не используем: «3» — это id, а не книга 3)
        doc = next((x for x in docs if str(x["id"]) == d), None) or next(
            (x for x in docs if Path(x["filename"]).stem == d or x["filename"] == d), None)
        if doc is None:
            raise SystemExit(f"{path}:{i}: неизвестный документ «{d}»")
        g["document_id"] = doc["id"]
        g["pdf_page"] = int(g["pdf_page"]) if g["pdf_page"] else None
        g["row"] = i
        out.append(g)
    return out


# --------------------------------------------------------------------------- значения извлечённой записи

def ext_value(o: dict, field: str) -> str | None:
    d = load()
    t = d.taxon(o.get("taxon_id"))
    if field == "date":
        return o.get("date_raw")
    if field == "year":
        return str(o["obs_year"]) if o.get("obs_year") else None
    if field == "species_ru":
        return (t or {}).get("name_ru") or (o["object_raw"] if o.get("object_type") == "taxon" else None)
    if field == "species_lat":
        if t:
            return t["name_lat"]
        m = _LAT_BINOM_RE.search(clean_name_keep_case(o.get("object_raw") or ""))
        return m.group(0) if m else None
    if field == "toponym":
        return o.get("toponym_raw")
    if field == "habitat":
        return o.get("habitat_raw")
    if field == "numeric":
        return o.get("value_raw") or (f"{o['value_num']:g}" if o.get("value_num") is not None else None)
    if field == "phenophase":
        p = d.phenophase(o.get("phenophase_id"))
        return f"{p['code']} {p['name_ru']}" if p else o.get("phenophase_raw")
    if field == "observer":
        return o.get("observers_raw")
    if field == "page":
        return o.get("printed_pages")
    return None


def _pages(s: str | None) -> set[int]:
    s = re.sub(r",?\s*том\s*\d+", "", s or "")
    out: set[int] = set()
    for a, b in re.findall(r"(\d+)(?:\s*-\s*(\d+))?", s):
        a, b = int(a), int(b or a)
        out |= set(range(a, b + 1)) if b - a < 50 else {a, b}
    return out


def _surnames(s: str | None) -> set[str]:
    ids, refs = match_people(s)
    return {f"id{r.person_id}" if r.person_id else fold(r.surname)[:5] for r in refs}


def correct(field: str, g: dict, o: dict) -> bool:
    gv, ev = g.get(field), ext_value(o, field)
    if gv is None or ev is None:
        return False
    if field == "date":
        gy = int(g["year"]) if g.get("year") and str(g["year"]).isdigit() else o.get("obs_year")
        gd = parse_date(gv, gy)
        if not gd.ok or not o.get("date_from"):
            return False
        return gd.date_from <= o["date_from"] and o["date_to"] <= gd.date_to or (gd.date_from == o["date_from"])
    if field == "year":
        return str(gv).strip()[:4] == ev
    if field in ("species_ru", "species_lat"):
        tg = match_taxon(gv)
        if tg.taxon_id and o.get("taxon_id"):
            return tg.taxon_id == o["taxon_id"]
        other = ev if field == "species_lat" else (o.get("object_raw") or ev)
        return fuzz.token_set_ratio(_words(gv), _words(other)) >= 85
    if field == "toponym":
        pg, pe = match_place(gv), o.get("place_id")
        if pg.place_id and pe:
            return pg.place_id == pe
        return fuzz.partial_ratio(fold(gv), fold(ev)) >= 85
    if field == "habitat":
        return fuzz.token_set_ratio(fold(gv), fold(ev)) >= 80
    if field == "numeric":
        a, b = numbers(gv), numbers(ev)
        return bool(a) and bool(b) and a[0] == b[0]
    if field == "phenophase":
        pg, _ = match_phenophase(gv, group=None)
        if pg is None:
            pg, _ = match_phenophase(gv, group="птицы")
        return pg is not None and pg == o.get("phenophase_id")
    if field == "observer":
        return _surnames(gv) == _surnames(ev) and bool(_surnames(gv))
    if field == "page":
        gp, ep = _pages(gv), _pages(ev)
        return bool(gp) and bool(gp & ep)
    return False


def _words(s: str | None) -> str:
    return " ".join(re.findall(r"[а-яa-z0-9]+", fold(s)))


def object_sim(g: dict, o: dict) -> float:
    best = 0.0
    for key in ("species_ru", "species_lat"):
        gv = g.get(key)
        if not gv:
            continue
        tg = match_taxon(gv)
        if tg.taxon_id and tg.taxon_id == o.get("taxon_id"):
            return 100.0
        best = max(best, fuzz.token_set_ratio(_words(gv), _words(o.get("object_raw"))))
    if best == 0 and g.get("event"):
        best = fuzz.token_set_ratio(fold(g["event"]), fold(o.get("object_raw") or "")) * 0.9
    return best


def pair_score(g: dict, o: dict) -> float | None:
    if g["document_id"] != o["document_id"]:
        return None
    op = (o.get("locator") or {}).get("pdf_page")
    if g["pdf_page"] and op and abs(g["pdf_page"] - op) > 1:
        return None
    s = object_sim(g, o)
    if s < 70:
        return None
    # цитаты должны пересекаться по словам: иначе это другое наблюдение того же вида
    if g.get("quote") and o.get("quote"):
        gw, ow = set(_words(g["quote"]).split()), set(_words(o["quote"]).split())
        if gw and len(gw & ow) / len(gw) < 0.6:
            return None
    score = s / 100
    if correct("date", g, o):
        score += 1.0
    elif g.get("date") is None and o.get("date_raw") is None:
        score += 0.5
    if correct("year", g, o):
        score += 0.5
    if correct("numeric", g, o):
        score += 0.5
    if g["pdf_page"] == op:
        score += 0.3
    return score


def evaluate(gold: list[dict], obs: list[dict], scope: str = "object") -> dict:
    pairs = []
    for gi, g in enumerate(gold):
        for oi, o in enumerate(obs):
            s = pair_score(g, o)
            if s is not None:
                pairs.append((s, gi, oi))
    pairs.sort(reverse=True)
    g_used, o_used, match = set(), set(), {}
    for s, gi, oi in pairs:
        if gi in g_used or oi in o_used:
            continue
        g_used.add(gi)
        o_used.add(oi)
        match[gi] = oi
    gold_pages = {(g["document_id"], g["pdf_page"]) for g in gold}
    in_scope = []
    for oi, o in enumerate(obs):
        key = (o["document_id"], (o.get("locator") or {}).get("pdf_page"))
        if key not in gold_pages:
            continue
        if scope == "object" and oi not in o_used:
            same_page = [g for g in gold if (g["document_id"], g["pdf_page"]) == key]
            if not any(object_sim(g, o) >= 85 for g in same_page):
                continue
        in_scope.append(oi)
    spurious = [oi for oi in in_scope if oi not in o_used]

    tp = {f: 0 for f in FIELDS}
    fp = dict(tp)
    fn = dict(tp)
    n = {f: sum(1 for g in gold if g.get(f)) for f in FIELDS}
    errors = []
    for gi, g in enumerate(gold):
        if gi not in match:
            for f in FIELDS:
                if g.get(f):
                    fn[f] += 1
            errors.append(_err("missed", None, g, None, g.get("species_ru") or g.get("event"), None,
                               "запись эталона не найдена среди извлечённых"))
            continue
        o = obs[match[gi]]
        for f in FIELDS:
            gv, ev = g.get(f), ext_value(o, f)
            if gv and ev and correct(f, g, o):
                tp[f] += 1
            else:
                if ev:
                    fp[f] += 1
                if gv:
                    fn[f] += 1
                if gv or ev:
                    errors.append(_err("wrong_value", f, g, o, gv, ev, None))
    for oi in spurious:
        o = obs[oi]
        for f in FIELDS:
            if ext_value(o, f):
                fp[f] += 1
        errors.append({"kind": "spurious", "field": None, "document_id": o["document_id"],
                       "pdf_page": (o.get("locator") or {}).get("pdf_page"), "observation_id": o["id"],
                       "expected": None, "actual": o.get("object_raw"), "quote": o.get("quote"),
                       "comment": "лишняя запись (нет в эталоне)"})

    def metric(name, t, p_, f_, nn):
        prec = t / (t + p_) if t + p_ else None
        rec = t / (t + f_) if t + f_ else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
        r = lambda x: round(x, 3) if x is not None else None  # noqa: E731
        return {"field": name, "precision": r(prec), "recall": r(rec), "f1": r(f1), "n": nn}

    rec_level = metric("record", len(match), len(spurious), len(gold) - len(match), len(gold))
    by_doc = []
    for did in sorted({g["document_id"] for g in gold}):
        gm = [gi for gi in match if gold[gi]["document_id"] == did]
        ng = sum(1 for g in gold if g["document_id"] == did)
        sp = sum(1 for oi in spurious if obs[oi]["document_id"] == did)
        by_doc.append({"document_id": did, "record_level": metric("record", len(gm), sp, ng - len(gm), ng)})
    # примеры: сначала пропуски и неверные значения
    order = {"missed": 0, "wrong_value": 1, "spurious": 2}
    errors.sort(key=lambda e: order[e["kind"]])
    return {
        "record_level": rec_level,
        "fields": [metric(f, tp[f], fp[f], fn[f], n[f]) for f in FIELDS],
        "by_document": by_doc,
        "error_examples": errors[:40],
        "n_gold_records": len(gold),
        "n_extracted_records": len(in_scope),
        "matches": {gold[gi]["row"]: obs[oi]["id"] for gi, oi in match.items()},
    }


def _err(kind, field, g, o, expected, actual, comment):
    return {"kind": kind, "field": field, "document_id": g["document_id"], "pdf_page": g["pdf_page"],
            "observation_id": o["id"] if o else None, "expected": expected, "actual": actual,
            "quote": (o or {}).get("quote") or g.get("quote"), "comment": comment}


def unsupported_pages(data: Path) -> list[dict]:
    out = []
    for d in load().documents:
        chunks = data / "chunks" / f"{d['id']}.jsonl"
        raw = data / "raw" / d["filename"]
        if not d.get("has_text_layer") and not chunks.exists():
            out.append({"document_id": d["id"], "pdf_page": None, "reason": "no_text_layer",
                        "comment": "скан без текстового слоя: нужен OCR (pipeline.ingest + Yandex Vision); не обработан"})
            continue
        if d.get("has_text_layer") and raw.exists():
            try:
                import fitz

                doc = fitz.open(str(raw))
                for i, p in enumerate(doc, 1):
                    if len(p.get_text().strip()) < 50:
                        out.append({"document_id": d["id"], "pdf_page": i, "reason": "image_only",
                                    "comment": "страница без текстового слоя (картинка) в PDF с текстом"})
            except Exception:  # noqa: BLE001
                pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "gold" / "sanity_2020.csv"))
    ap.add_argument("--observations", nargs="*", help="jsonl с записями (по умолчанию data/observations/*.jsonl)")
    ap.add_argument("--scope", choices=["object", "page"], default="object")
    ap.add_argument("--out", default=str(ROOT / "data" / "quality.json"))
    a = ap.parse_args(argv)
    data = ROOT / "data"
    gold_p = Path(a.gold)
    gold = read_gold(gold_p)
    files = [Path(x) for x in a.observations] if a.observations else sorted(
        p for p in (data / "observations").glob("*.jsonl"))
    obs = [json.loads(x) for p in files for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    res = evaluate(gold, obs, a.scope)
    sha = hashlib.sha256(gold_p.read_bytes()).hexdigest()[:8]
    q = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gold_version": f"{gold_p.name}@{sha} (scope={a.scope})",
        "extractor_version": EXTRACTOR_VERSION, "is_placeholder": False,
        "n_gold_records": res["n_gold_records"], "n_extracted_records": res["n_extracted_records"],
        "record_level": res["record_level"], "fields": res["fields"], "by_document": res["by_document"],
        "error_examples": res["error_examples"], "unsupported": unsupported_pages(data),
    }
    _validate(q)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.out}: эталон {q['n_gold_records']}, извлечено в области {q['n_extracted_records']}")
    rl = q["record_level"]
    print(f"{'record':12} P={rl['precision']} R={rl['recall']} F1={rl['f1']} n={rl['n']}")
    for m in q["fields"]:
        print(f"{m['field']:12} P={m['precision']} R={m['recall']} F1={m['f1']} n={m['n']}")
    return q


def _validate(q: dict):
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "contracts" / "observation.schema.json").read_text(encoding="utf-8"))
    sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/Quality"}
    Draft202012Validator(sub).validate(q)


if __name__ == "__main__":
    main()
