"""Интеграция run.py с LLM_BACKEND=mock: страницы 69–70 (таблица) и 449–450 (текст + подписи)."""
import json

import pytest

from pipeline.eval.evaluate import evaluate, read_gold
from pipeline.extract.llm import LLMClient, LLMUnavailable
from pipeline.extract.prompt import LLMResponse, build_messages, PromptContext, response_json_schema

from .conftest import ROOT, needs_pdf


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LETOPISI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LETOPISI_RAW_DIR", str(ROOT / "data" / "raw"))
    monkeypatch.setenv("LLM_BACKEND", "mock")
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
    monkeypatch.delenv("LLM_MOCK_FILE", raising=False)
    return tmp_path


def _validator():
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "contracts" / "observation.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


@needs_pdf
def test_run_pages_69_70(env):
    from pipeline.run import run

    rep = run("3", "69-70", quiet=True)
    obs = [json.loads(x) for x in (env / "observations" / "3.jsonl").read_text(encoding="utf-8").splitlines()]
    chunks = {c["id"]: c for c in map(json.loads, (env / "chunks" / "3.jsonl").read_text(encoding="utf-8").splitlines())}
    assert rep["invalid_dropped"] == 0 and rep["observations"] == len(obs) > 70
    v = _validator()
    for o in obs:
        assert not list(v.iter_errors(o))
        c = chunks[o["chunk_id"]]
        assert c["text"][o["quote_span"][0]:o["quote_span"][1]] == o["quote"]  # контракт, п. 4
        assert ("year_outside_book" in o["flags"]) == (o["obs_year"] != 2020)
        if o["date_precision"] == "day":
            assert o["date_from"] == o["date_to"]
    ger = [o for o in obs if o["taxon_id"] == 4 and o["event_raw"].startswith("зацветание")]
    assert sorted((o["obs_year"], o["date_from"]) for o in ger) == [
        (2018, "2018-05-18"), (2019, "2019-06-11"), (2020, "2020-06-03")]
    assert all(o["phenophase_id"] == 5 and o["person_ids"] == [5, 11, 7, 6] and o["bboxes"] for o in ger)
    assert json.loads((env / "sections" / "3.json").read_text(encoding="utf-8"))
    assert rep["llm_windows"] >= 1 and rep["llm_usage"]["calls"] == rep["llm_windows"]


@needs_pdf
def test_run_449_450_llm_mock_and_eval(env):
    from pipeline.run import run

    rep = run("3", "449-450", quiet=True)
    obs = [json.loads(x) for x in (env / "observations" / "3.jsonl").read_text(encoding="utf-8").splitlines()]
    llm = [o for o in obs if o["event_raw"] == "приходили есть яблоки"]
    assert len(llm) == 1
    o = llm[0]
    assert o["date_from"] == "2020-07-01" and o["date_precision"] == "month" and o["place_id"] == 4
    assert o["person_ids"] == [8] and o["flags"] == ["taxon_unmatched"]  # полевки нет в стартовом справочнике
    bad = [o for o in obs if o["object_raw"] == "полёвки"]
    assert bad and "quote_not_found" in bad[0]["flags"] and bad[0]["quote_span"] is None and bad[0]["confidence"] <= 0.5
    moose = next(o for o in obs if o["taxon_id"] == 16)
    assert moose["value_num"] == 32 and moose["observers_raw"] == "Валовой Е.В."
    gold = read_gold(ROOT / "gold" / "sanity_2020.csv")
    res = evaluate([g for g in gold if g["pdf_page"] in (449, 450)], obs, "object")
    assert res["record_level"]["recall"] >= 0.85
    assert rep["flags"].get("quote_not_found") == 1


def test_mock_client_and_schema(tmp_path, monkeypatch):
    f = tmp_path / "m.json"
    f.write_text(json.dumps([{"match": "лось", "records": [
        {"record_type": "observation", "object_type": "taxon", "object_raw": "лось", "quote": "лось вышел"}]}]),
        encoding="utf-8")
    monkeypatch.setenv("LLM_MOCK_FILE", str(f))
    c = LLMClient(backend="mock")
    msgs = build_messages(PromptContext("Книга", 2020, chunks=["лось вышел к дороге"]))
    r = c.extract(msgs)
    assert isinstance(r, LLMResponse) and r.records[0].chunk_ref == 1
    assert c.extract(build_messages(PromptContext("Книга", 2020, chunks=["ничего"]))).records == []
    assert c.cache_key(msgs) != c.cache_key(msgs[:-1])
    assert response_json_schema()["properties"]["records"]["type"] == "array"


def test_no_key_raises(monkeypatch):
    monkeypatch.delenv("YC_API_KEY", raising=False)
    monkeypatch.delenv("YC_FOLDER_ID", raising=False)
    c = LLMClient(backend="yandex", cache_dir=None)
    with pytest.raises(LLMUnavailable):
        c._openai()
