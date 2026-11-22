"""Тестовая БД для поиска: настоящий PostgreSQL.

Переменная ``TEST_DATABASE_URL`` — подключение к серверу (например
``postgresql://postgres@localhost:55433/postgres``). Тесты создают на нём временную БД
``letopisi_search_<pid>_<rnd>`` с той же локалью (TEMPLATE template0), применяют
``contracts/schema.sql``, загружают данные и в конце удаляют БД. Нет переменной или сервер
недоступен — все тесты пропускаются с сообщением. Если локаль сервера такова, что pg_trgm не
видит кириллицу (LC_CTYPE=C, см. contracts/README.md, п. 15), — тоже пропуск с объяснением.

Что загружается:
1. ``contracts/fixtures/*`` целиком (документы, разделы, фрагменты, справочники, 38 записей,
   правки, сохранённые запросы); ``person_ids`` → ``observation_persons``.
2. Снимок ядра ``data/observations_3_pages.jsonl`` (572 записи книги 2020, id 30000001+)
   и его разделы ``data/sections_3.json`` (id 300xx). Папку можно подменить переменной
   ``SEARCH_SNAPSHOT_DIR``. Ссылки снимка проверяются по уже загруженным справочникам:
   ``taxon_id`` / ``place_id`` / ``phenophase_id`` / ``section_id`` / ``chunk_id``, которых нет в БД,
   заменяются на NULL (запись остаётся), ``person_ids`` без пары в ``persons`` отбрасываются,
   запись с несуществующим ``document_id`` пропускается. Счётчики — в ``LOAD_REPORT``.
   В текущем снимке это касается только ``chunk_id`` (фрагменты 3000xxx не входят в фикстуры).

Загрузчик свой (``pipeline/load.py`` — файл другого участника).
"""
from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIX = ROOT / "contracts" / "fixtures"
SCHEMA = ROOT / "contracts" / "schema.sql"
SNAP_DIR = Path(os.environ.get("SEARCH_SNAPSHOT_DIR") or Path(__file__).parent / "data")

LOAD_REPORT: dict = {}

JSONB_COLS = {"locator", "bboxes", "flags", "lines", "changes", "filters"}


def _psycopg():
    try:
        import psycopg
        return psycopg
    except ImportError:
        pytest.skip("нет пакета psycopg (pip install -r backend/requirements-search.txt)", allow_module_level=True)


def _json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _columns(cur, table: str) -> list[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s AND table_schema = 'public' "
                "ORDER BY ordinal_position", (table,))
    return [r[0] for r in cur.fetchall()]


def _insert(cur, table: str, rows: list[dict]) -> int:
    from psycopg import sql
    from psycopg.types.json import Jsonb

    if not rows:
        return 0
    cols = [c for c in _columns(cur, table) if any(c in r for r in rows)]
    q = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)))
    data = []
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c)
            if c in JSONB_COLS and v is not None:
                v = Jsonb(v)
            vals.append(v)
        data.append(vals)
    cur.executemany(q, data)
    return len(rows)


def load_all(conn) -> dict:
    """Загрузить фикстуры + снимок ядра. Возвращает отчёт загрузки."""
    rep: dict = {"fixtures": {}, "snapshot": {}}
    with conn.cursor() as cur:
        for table, name in [("documents", "documents.json")]:
            rep["fixtures"][table] = _insert(cur, table, _json(name))
        # разделы: фикстуры + снимок; parent_id — вторым проходом
        sections = _json("sections.json")
        snap_sections = json.loads((SNAP_DIR / "sections_3.json").read_text(encoding="utf-8")) \
            if (SNAP_DIR / "sections_3.json").exists() else []
        all_sections = sections + [s for s in snap_sections if s["id"] not in {x["id"] for x in sections}]
        parents = {s["id"]: s.get("parent_id") for s in all_sections}
        _insert(cur, "sections", [{**s, "parent_id": None} for s in all_sections])
        for sid, pid in parents.items():
            if pid is not None:
                cur.execute("UPDATE sections SET parent_id = %s WHERE id = %s", (pid, sid))
        rep["fixtures"]["sections"] = len(sections)
        rep["snapshot"]["sections"] = len(snap_sections)
        chunks = _jsonl(FIX / "chunks.jsonl")
        rep["fixtures"]["chunks"] = _insert(cur, "chunks", chunks)
        for table in ("taxa", "taxon_names", "places", "place_names", "persons", "phenophases"):
            rep["fixtures"][table] = _insert(cur, table, _json(f"{table}.json"))

        obs = _jsonl(FIX / "observations.jsonl")
        rep["fixtures"]["observations"] = _insert(cur, "observations", obs)
        links = [{"observation_id": o["id"], "person_id": p, "ord": i}
                 for o in obs for i, p in enumerate(o.get("person_ids") or [])]
        rep["fixtures"]["observation_persons"] = _insert(cur, "observation_persons", links)
        rep["fixtures"]["observation_edits"] = _insert(cur, "observation_edits", _jsonl(FIX / "observation_edits.jsonl"))
        rep["fixtures"]["saved_queries"] = _insert(cur, "saved_queries", _json("saved_queries.json"))

        # снимок ядра
        snap_path = SNAP_DIR / "observations_3_pages.jsonl"
        if snap_path.exists():
            snap = _jsonl(snap_path)
            existing = {}
            for table, key in [("taxa", "taxon_id"), ("places", "place_id"), ("phenophases", "phenophase_id"),
                               ("sections", "section_id"), ("chunks", "chunk_id"), ("documents", "document_id"),
                               ("persons", "person")]:
                cur.execute(f"SELECT id FROM {table}")  # имя таблицы — из списка выше
                existing[key] = {r[0] for r in cur.fetchall()}
            nulled: Counter = Counter()
            dropped_persons = 0
            skipped = 0
            rows, links = [], []
            fixture_ids = {o["id"] for o in obs}
            for o in snap:
                if o["document_id"] not in existing["document_id"] or o["id"] in fixture_ids:
                    skipped += 1
                    continue
                o = dict(o)
                for key in ("taxon_id", "place_id", "phenophase_id", "section_id", "chunk_id"):
                    if o.get(key) is not None and o[key] not in existing[key]:
                        o[key] = None
                        nulled[key] += 1
                pids = []
                for p in o.get("person_ids") or []:
                    if p in existing["person"]:
                        pids.append(p)
                    else:
                        dropped_persons += 1
                rows.append(o)
                links += [{"observation_id": o["id"], "person_id": p, "ord": i} for i, p in enumerate(pids)]
            rep["snapshot"]["observations"] = _insert(cur, "observations", rows)
            rep["snapshot"]["observation_persons"] = _insert(cur, "observation_persons", links)
            rep["snapshot"]["nulled_fk"] = dict(nulled)
            rep["snapshot"]["dropped_person_links"] = dropped_persons
            rep["snapshot"]["skipped_records"] = skipped
        else:
            rep["snapshot"]["missing"] = str(snap_path)

        for table in ("documents", "sections", "chunks", "taxa", "taxon_names", "places", "place_names", "persons",
                      "phenophases", "observations", "observation_edits", "saved_queries"):
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1))")
    conn.commit()
    return rep


@pytest.fixture(scope="session")
def pg_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан — тесты поиска на PostgreSQL пропущены "
                    "(пример: postgresql://postgres@localhost:5432/postgres)")
    psycopg = _psycopg()
    base = url.replace("postgresql+psycopg://", "postgresql://")
    try:
        admin = psycopg.connect(base, autocommit=True, connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL по TEST_DATABASE_URL недоступен: {type(e).__name__}")
    ctype, collate = admin.execute(
        "SELECT datctype, datcollate FROM pg_database WHERE datname = current_database()").fetchone()
    name = f"letopisi_search_{os.getpid()}_{random.randint(1000, 9999)}"
    from psycopg import sql

    admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8' LC_CTYPE {} LC_COLLATE {}").format(
        sql.Identifier(name), sql.Literal(ctype), sql.Literal(collate)))
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    info = conninfo_to_dict(base)
    info["dbname"] = name
    test_url = make_conninfo(**info)
    try:
        with psycopg.connect(test_url) as c:
            c.execute(SCHEMA.read_text(encoding="utf-8"))
            c.commit()
            if c.execute("SELECT show_trgm('лось') = '{}'::text[]").fetchone()[0]:
                pytest.skip(f"LC_CTYPE={ctype}: pg_trgm не видит кириллицу — создайте сервер/БД с ru_RU.UTF-8 / "
                            "C.UTF-8 / ru-RU (contracts/README.md, п. 15)")
            LOAD_REPORT.update(load_all(c))
        yield test_url
    finally:
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        admin.close()


@pytest.fixture(scope="session")
def engine(pg_url):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    u = make_url("postgresql+psycopg://")
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(pg_url)
    u = u.set(username=info.get("user"), password=info.get("password"), host=info.get("host"),
              port=int(info["port"]) if info.get("port") else None, database=info.get("dbname"))
    eng = create_engine(u, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def conn(engine):
    """Соединение в транзакции, которая откатывается после теста (тесты могут добавлять записи)."""
    c = engine.connect()
    tx = c.begin()
    try:
        yield c
    finally:
        tx.rollback()
        c.close()


@pytest.fixture()
def client(conn, monkeypatch):
    """FastAPI TestClient с роутером поиска; get_conn → то же соединение в транзакции."""
    monkeypatch.setenv("LLM_BACKEND", "mock")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.routers import search as search_router

    app = FastAPI()
    app.include_router(search_router.router)

    def _conn():
        yield conn

    app.dependency_overrides[search_router.get_conn] = _conn
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """Разбор запросов в тестах — только правилами (LLM не вызывается)."""
    monkeypatch.setenv("LLM_BACKEND", "mock")


@pytest.fixture()
def add_obs(conn):
    """Добавить синтетическую запись (откатится вместе с транзакцией теста)."""
    from sqlalchemy import text

    counter = {"n": 0}

    def _add(**kw) -> int:
        counter["n"] += 1
        rid = 90_000_000 + counter["n"]
        row = {"id": rid, "record_type": "observation", "object_type": "taxon", "object_raw": "тест",
               "document_id": 3, "locator": json.dumps({"pdf_page": 1, "half": None, "printed_page": None}),
               "extractor_version": "test-synthetic", "status": "auto", "flags": "[]", "obs_year": 2020}
        persons = kw.pop("person_ids", [])
        for k in ("flags", "locator", "bboxes"):
            if k in kw and not isinstance(kw[k], str):
                kw[k] = json.dumps(kw[k])
        row.update(kw)
        cols = list(row)
        casts = {"locator": "jsonb", "flags": "jsonb", "bboxes": "jsonb"}
        vals = ", ".join(f"CAST(:{c} AS {casts[c]})" if c in casts else f":{c}" for c in cols)
        conn.execute(text(f"INSERT INTO observations ({', '.join(cols)}) VALUES ({vals})"), row)
        for i, p in enumerate(persons):
            conn.execute(text("INSERT INTO observation_persons (observation_id, person_id, ord) VALUES (:o, :p, :i)"),
                         {"o": rid, "p": p, "i": i})
        return rid

    return _add


@pytest.fixture(scope="session")
def saved_queries():
    return {q["id"]: q for q in _json("saved_queries.json")}
