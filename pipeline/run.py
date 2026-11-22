"""CLI ядра: chunks → разделы → маршрутизация → извлечение → привязка → нормализация → флаги → валидация.

    python -m pipeline.run --document 3 [--pages 69-79,449-455] [--sections 7.3] [--dry-run]

``--document`` — id документа (contracts/fixtures/documents.json) или имя файла без расширения
(``book27_2020``). Вход — ``data/chunks/<id>.jsonl`` (пишет pipeline/ingest); если файла нет, а PDF
с текстовым слоем лежит в data/raw, chunks строятся временным дампером ``pipeline.devtools.quick_chunks``.

Выход:
- ``data/sections/<id>.json`` — разделы по контракту, ``data/sections/<id>.authors.json`` — авторы разделов;
- ``data/chunks/<id>.jsonl`` — те же chunks с проставленным section_id;
- ``data/observations/<id>.jsonl`` — записи, каждая проверена по contracts/observation.schema.json;
- ``data/observations/<id>.report.json`` — отчёт (записей, по стратегиям, флаги, токены, оценка стоимости).

``--dry-run`` ничего не запрашивает у LLM: считает окна, число вызовов (без учёта кэша) и токены.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.extract.extractor import EXTRACTOR_VERSION, DocContext, build, draft_from_llm
from pipeline.extract.llm import LLMClient, LLMUnavailable, load_dotenv
from pipeline.extract.prompt import PROMPT_VERSION, PromptContext, build_messages
from pipeline.extract.router import Window, route, windows
from pipeline.normalize.dicts import ROOT, load
from pipeline.sections import build_sections

log = logging.getLogger("pipeline.run")


def data_dir() -> Path:
    return Path(os.environ.get("LETOPISI_DATA_DIR") or ROOT / "data")


def find_document(key: str) -> dict:
    docs = load().documents
    for d in docs:
        if str(d["id"]) == str(key) or Path(d["filename"]).stem == key or d["filename"] == key:
            return d
    raise SystemExit(f"документ «{key}» не найден в contracts/fixtures/documents.json")


def parse_pages(s: str | None) -> list[tuple[int, int]] | None:
    if not s:
        return None
    out = []
    for part in s.split(","):
        a, _, b = part.strip().partition("-")
        out.append((int(a), int(b or a)))
    return out


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(p: Path, rows) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_chunks(doc: dict, ddir: Path, pages: list[tuple[int, int]] | None) -> tuple[list[dict], dict]:
    p = ddir / "chunks" / f"{doc['id']}.jsonl"
    if not p.exists():
        raw = Path(os.environ.get("LETOPISI_RAW_DIR") or ddir / "raw") / doc["filename"]
        if doc.get("format") == "pdf" and doc.get("has_text_layer") and raw.exists():
            from pipeline.devtools import quick_chunks

            rng = None
            if pages:  # дампим только нужный диапазон (+1 страница до — для заголовков таблиц)
                rng = (max(1, min(a for a, _ in pages) - 1), max(b for _, b in pages))
            print(f"chunks нет — временный дампер pipeline.devtools.quick_chunks ({raw.name}, стр. {rng or 'все'})",
                  file=sys.stderr)
            ch, ln = quick_chunks.dump(raw, doc["id"], rng)
            quick_chunks.write(ch, ln, p.parent, doc["id"])
        else:
            raise SystemExit(f"нет {p}: сначала запустите pipeline.ingest (скан без текстового слоя)")
    chunks = read_jsonl(p)
    lines: dict = {}
    lp = p.with_name(f"{doc['id']}.lines.jsonl")
    if lp.exists():
        for r in read_jsonl(lp):
            lines[r["chunk_id"]] = r
    return chunks, lines


def _in_pages(c: dict, pages) -> bool:
    if not pages:
        return True
    p = (c.get("locator") or {}).get("pdf_page")
    return p is not None and any(a <= p <= b for a, b in pages)


def _descendants(sections: list[dict], codes: list[str]) -> set[int]:
    ids = {s["id"] for s in sections if s.get("code") in codes}
    changed = True
    while changed:
        changed = False
        for s in sections:
            if s["parent_id"] in ids and s["id"] not in ids:
                ids.add(s["id"])
                changed = True
    return ids


def prompt_for(w: Window, doc: dict, sres, chunk_by_id: dict) -> list[dict]:
    sec = sres.section(w.section_id) if w.section_id else None
    auth = sres.authors_for(w.section_id) if w.section_id else None
    loc = w.chunks[0].get("locator") or {}
    ctx = PromptContext(
        book_title=doc["title"], book_year=doc.get("year"),
        section_code=sec.get("code") if sec else None, section_title=sec.get("title") if sec else None,
        section_authors=auth["authors_raw"] if auth else None,
        printed_page=loc.get("printed_page"), pdf_page=loc.get("pdf_page"),
        prev_text=w.prev_text, table_caption=w.ctx.caption if w.ctx else None,
        table_group=w.ctx.group if w.ctx else None, obs_year_hint=doc.get("year"),
        chunks=[c["text"] for c in w.chunks],
    )
    return build_messages(ctx)


def _validator():
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "contracts" / "observation.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def run(document: str, pages_s: str | None = None, sections_s: str | None = None, dry_run: bool = False,
        skip_sections: bool = True, quiet: bool = False) -> dict:
    load_dotenv()
    ddir = data_dir()
    doc = find_document(document)
    pages = parse_pages(pages_s)
    t0 = time.time()
    chunks, lines = load_chunks(doc, ddir, pages)
    last_page = doc.get("pages")
    sres = build_sections(doc["id"], chunks, last_page)
    for c in chunks:
        c["section_id"] = sres.chunk_section.get(c["id"])
    write_jsonl(ddir / "chunks" / f"{doc['id']}.jsonl", chunks)
    sdir = ddir / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{doc['id']}.json").write_text(json.dumps(sres.sections, ensure_ascii=False, indent=1), encoding="utf-8")
    (sdir / f"{doc['id']}.authors.json").write_text(
        json.dumps({str(k): v for k, v in sres.authors.items()}, ensure_ascii=False, indent=1), encoding="utf-8")

    sel_sections = _descendants(sres.sections, [s.strip() for s in sections_s.split(",")]) if sections_s else None
    titles = {s["id"]: s["title"] for s in sres.sections}
    routed = route(chunks, sres.chunk_section, titles, sres.toc_pages, doc.get("year"), skip_sections)
    keep = lambda c: _in_pages(c, pages) and (sel_sections is None or c.get("section_id") in sel_sections)  # noqa: E731
    routed = [r for r in routed if keep(r.chunk)]
    wins = windows(routed, sres.chunk_section)
    strategies = Counter(r.strategy for r in routed)
    chunk_by_id = {c["id"]: c for c in chunks}
    dc = DocContext(doc["id"], doc.get("year"), doc["title"], sres, lines)
    client = LLMClient()
    cpt = float(os.environ.get("LLM_CHARS_PER_TOKEN", "3.0"))
    price = float(os.environ.get("LLM_PRICE_RUB_PER_1K", "0.40"))

    report: dict = {
        "document_id": doc["id"], "filename": doc["filename"], "pages": pages_s, "sections": sections_s,
        "extractor_version": EXTRACTOR_VERSION, "prompt_version": PROMPT_VERSION, "model": client.model,
        "llm_backend": client.backend, "chunks_total": len(chunks), "chunks_selected": len(routed),
        "sections_found": len(sres.sections), "sections_mode": sres.mode,
        "routing": dict(strategies), "llm_windows": len(wins),
    }

    # ----- dry-run: оценка без запросов
    msgs_all = [prompt_for(w, doc, sres, chunk_by_id) for w in wins]
    in_chars = sum(sum(len(m["content"]) for m in ms) for ms in msgs_all)
    chunk_chars = sum(len(c["text"]) for w in wins for c in w.chunks)
    est_in = int(in_chars / cpt)
    est_out = int(len(wins) * 60 + 0.6 * chunk_chars / cpt)
    cached = sum(client.is_cached(ms) for ms in msgs_all) if client.backend != "mock" else 0
    report["estimate"] = {
        "llm_calls": len(wins), "already_cached": cached, "input_tokens": est_in, "output_tokens": est_out,
        "chars_per_token": cpt, "price_rub_per_1k_tokens": price,
        "cost_rub": round((est_in + est_out) / 1000 * price, 2),
        "note": "оценка: токены = символы / chars_per_token; цена — LLM_PRICE_RUB_PER_1K (проверьте прайс AI Studio)",
    }
    if dry_run:
        report["dry_run"] = True
        report["seconds"] = round(time.time() - t0, 1)
        _save_report(ddir, doc, report, quiet)
        return report

    # ----- детерминированные записи
    drafts: list[tuple] = []  # (draft, chunk)
    for r in routed:
        for d in r.drafts:
            drafts.append((d, r.chunk))

    # ----- LLM
    llm_error = None

    def do(i):
        return i, client.extract(msgs_all[i])

    if wins:
        try:
            with ThreadPoolExecutor(max_workers=max(1, client.concurrency)) as ex:
                for i, resp in ex.map(do, range(len(wins))):
                    w = wins[i]
                    for rec in resp.records:
                        d = draft_from_llm(rec)
                        drafts.append((d, _pick_chunk(d, w)))
        except LLMUnavailable as e:
            llm_error = str(e)
            print(f"LLM недоступен: {e} — записи только из детерминированных парсеров", file=sys.stderr)
    report["llm_error"] = llm_error
    report["llm_usage"] = client.usage.as_dict()
    if client.usage.prompt_tokens:
        report["llm_usage"]["cost_rub"] = round(
            (client.usage.prompt_tokens + client.usage.completion_tokens) / 1000 * price, 2)

    # ----- сборка, дедупликация, валидация
    val = _validator()
    obs_out, invalid, seen = [], [], set()
    seq = 0
    by_strategy: Counter = Counter()
    for d, ch in sorted(drafts, key=lambda x: (x[1]["ord"], x[0].quote_span or (0, 0))):
        seq += 1
        o = build(d, ch, dc, doc["id"] * 10_000_000 + seq)
        key = (o["chunk_id"], o["object_raw"], o["date_raw"], o["value_raw"], o["indicator"], o["phenophase_id"],
               o["obs_year"])
        if key in seen:
            continue
        seen.add(key)
        errs = sorted(val.iter_errors(o), key=lambda e: e.path)
        if errs:
            invalid.append({"id": o["id"], "errors": [f"{list(e.path)}: {e.message}" for e in errs[:3]]})
            continue
        obs_out.append(o)
        by_strategy[d.strategy] += 1
    write_jsonl(ddir / "observations" / f"{doc['id']}.jsonl", obs_out)
    flags = Counter(f for o in obs_out for f in o["flags"])
    report.update({
        "observations": len(obs_out), "by_strategy": dict(by_strategy), "flags": dict(flags.most_common()),
        "flagged_records": sum(1 for o in obs_out if set(o["flags"]) - {"year_outside_book"}),
        "invalid_dropped": len(invalid), "invalid_examples": invalid[:5],
        "taxon_matched": sum(1 for o in obs_out if o["taxon_id"]),
        "mean_confidence": round(sum(o["confidence"] for o in obs_out) / len(obs_out), 3) if obs_out else None,
        "seconds": round(time.time() - t0, 1),
    })
    _save_report(ddir, doc, report, quiet)
    return report


def _pick_chunk(d, w: Window) -> dict:
    """Фрагмент окна, в котором действительно лежит цитата (модель могла ошибиться в chunk_ref)."""
    from pipeline.anchor import find_span

    order = list(range(len(w.chunks)))
    ref = d.chunk_ref - 1
    if 0 <= ref < len(w.chunks):
        order.remove(ref)
        order.insert(0, ref)
    for i in order:
        if find_span(d.quote, w.chunks[i]["text"])[0] is not None:
            return w.chunks[i]
    return w.chunks[order[0]]


def _save_report(ddir: Path, doc: dict, report: dict, quiet: bool) -> None:
    p = ddir / "observations" / (f"{doc['id']}.dryrun.json" if report.get("dry_run") else f"{doc['id']}.report.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if not quiet:
        print(json.dumps(report, ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ядро извлечения записей из летописи")
    ap.add_argument("--document", required=True, help="id документа или имя файла без расширения")
    ap.add_argument("--pages", help="страницы PDF: 69-79 или 69-79,449-455")
    ap.add_argument("--sections", help="коды разделов через запятую (с подразделами): 7.3,8.5")
    ap.add_argument("--dry-run", action="store_true", help="без запросов к LLM: число вызовов и токенов")
    ap.add_argument("--all-sections", action="store_true", help="не пропускать введение/приложения и т.п. для LLM")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(a.document, a.pages, a.sections, a.dry_run, not a.all_sections)


if __name__ == "__main__":
    main()
