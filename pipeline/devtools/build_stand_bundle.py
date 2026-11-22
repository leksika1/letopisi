"""Собирает каталог для ``pipeline/load.py`` из справочников контракта и результатов ядра.

Справочники, документы и сохранённые запросы берутся из ``contracts/fixtures``.
Для документов, по которым есть прогон ядра (``data/observations/<id>.jsonl``),
записи, фрагменты и разделы из фикстур заменяются результатами прогона — чтобы
в базе не было дублей. Строки фрагментов (``data/chunks/<id>.lines.jsonl``)
переносятся в ``Chunk.lines``, авторы разделов — в ``Section.authors_raw``.

    python -m pipeline.devtools.build_stand_bundle [--out data/stand_bundle]
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pipeline.extract.consistency import enforce

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "contracts" / "fixtures"
DATA = ROOT / "data"


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in f if line.strip()]
        return json.load(f)


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        if path.suffix == ".jsonl":
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        else:
            json.dump(rows, f, ensure_ascii=False, indent=1)


def _chunk_lines(chunk: dict, meta: dict | None) -> list[dict] | None:
    if not meta or not meta.get("lines"):
        return None
    out = []
    text = chunk["text"]
    for start, end, x0, y0, x1, y1 in meta["lines"]:
        out.append({"text": text[start:end], "start": start, "end": end,
                    "bbox": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]})
    return out


def build(out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    for p in FIX.iterdir():
        if p.is_file() and p.suffix in (".json", ".jsonl"):
            shutil.copy(p, out / p.name)

    run_docs = sorted(int(p.stem) for p in (DATA / "observations").glob("*.jsonl") if p.stem.isdigit())
    sections = [s for s in _read(FIX / "sections.json") if s["document_id"] not in run_docs]
    chunks = [c for c in _read(FIX / "chunks.jsonl") if c["document_id"] not in run_docs]
    obs = [o for o in _read(FIX / "observations.jsonl") if o["document_id"] not in run_docs]
    kept = {o["id"] for o in obs}
    edits = [e for e in _read(FIX / "observation_edits.jsonl") if e["observation_id"] in kept]

    for doc in run_docs:
        authors_p = DATA / "sections" / f"{doc}.authors.json"
        authors = json.loads(authors_p.read_text(encoding="utf-8")) if authors_p.exists() else {}
        for s in _read(DATA / "sections" / f"{doc}.json"):
            a = authors.get(str(s["id"])) or {}
            s["authors_raw"] = a.get("authors_raw")
            s["author_person_ids"] = a.get("person_ids") or []
            sections.append(s)
        lines_p = DATA / "chunks" / f"{doc}.lines.jsonl"
        lines = {m["chunk_id"]: m for m in _read(lines_p)} if lines_p.exists() else {}
        section_of = {}
        doc_obs = _read(DATA / "observations" / f"{doc}.jsonl")
        for o in doc_obs:
            if o.get("chunk_id") and o.get("section_id"):
                section_of.setdefault(o["chunk_id"], o["section_id"])
        for c in _read(DATA / "chunks" / f"{doc}.jsonl"):
            c["lines"] = _chunk_lines(c, lines.get(c["id"]))
            c["section_id"] = c.get("section_id") or section_of.get(c["id"])
            chunks.append(c)
        report_p = DATA / "observations" / f"{doc}.report.json"
        stamp = datetime.fromtimestamp(report_p.stat().st_mtime if report_p.exists() else 0, timezone.utc).isoformat()
        for o in doc_obs:
            enforce(o)  # прогоны до появления consistency.py
            o.setdefault("created_at", stamp)
            o.setdefault("updated_at", stamp)
        obs.extend(doc_obs)

    _write(out / "sections.json", sections)
    _write(out / "chunks.jsonl", chunks)
    _write(out / "observations.jsonl", obs)
    _write(out / "observation_edits.jsonl", edits)
    return {"documents_with_run": len(run_docs), "sections": len(sections), "chunks": len(chunks),
            "observations": len(obs), "observation_edits": len(edits)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DATA / "stand_bundle")
    print(json.dumps(build(ap.parse_args().out), ensure_ascii=False))


if __name__ == "__main__":
    main()
