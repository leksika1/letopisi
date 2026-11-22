"""Клиент LLM: OpenAI-совместимый API Yandex AI Studio + бэкенд «mock» для тестов.

Переменные окружения:
- ``YC_API_KEY``, ``YC_FOLDER_ID`` — ключ и каталог (ключ никогда не логируется);
- ``LLM_MODEL`` — по умолчанию ``gpt://<YC_FOLDER_ID>/yandexgpt/latest``
  (альтернатива ``gpt://<YC_FOLDER_ID>/qwen3-235b-a22b-fp8/latest``);
- ``LLM_BASE_URL`` — по умолчанию ``https://llm.api.cloud.yandex.net/v1``;
- ``LLM_CONCURRENCY`` — число параллельных запросов (по умолчанию 4);
- ``LLM_BACKEND=mock`` — без сети: ответы из ``LLM_MOCK_FILE`` (JSON-список
  ``{"match": "подстрока запроса", "records": [...]}``), по умолчанию ``pipeline/tests/data/llm_mock/responses.json``;
- ``LLM_CACHE_DIR`` — кэш ответов (по умолчанию ``data/llm_cache``).

Запрос: temperature 0, ``response_format = json_schema``; если модель/API его не принимает —
``json_object`` (запоминается до конца процесса). Ответ валидируется pydantic-моделью
``LLMResponse``; при ошибке — один повтор с текстом ошибки. 429/5xx/сетевые ошибки — до 5 повторов
с экспоненциальной паузой. Ответ кэшируется на диск по
``sha256(model + PROMPT_VERSION + system + user)`` (user содержит текст фрагментов и контекст).
Токены каждого реального запроса пишутся в ``<cache>/usage.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .prompt import PROMPT_VERSION, LLMResponse, response_json_schema

log = logging.getLogger("pipeline.llm")
ROOT = Path(__file__).resolve().parents[2]


class LLMUnavailable(RuntimeError):
    """Нет ключа/каталога или API недоступен — извлечение LLM невозможно."""


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0
    retries: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("calls", "cached", "prompt_tokens", "completion_tokens", "errors", "retries")}


def load_dotenv(path: Path | None = None) -> None:
    """Подхватить переменные из .env в корне (если они ещё не заданы). Значения не печатаются."""
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def default_model() -> str:
    m = os.environ.get("LLM_MODEL")
    if m:
        return m
    folder = os.environ.get("YC_FOLDER_ID", "<YC_FOLDER_ID>")
    return f"gpt://{folder}/yandexgpt/latest"


class LLMClient:
    def __init__(self, backend: str | None = None, model: str | None = None, cache_dir: Path | None = None):
        self.backend = backend or os.environ.get("LLM_BACKEND", "yandex")
        self.model = model or default_model()
        self.cache_dir = Path(cache_dir or os.environ.get("LLM_CACHE_DIR") or ROOT / "data" / "llm_cache")
        self.usage = Usage()
        self.concurrency = int(os.environ.get("LLM_CONCURRENCY", "4"))
        self._sem = threading.Semaphore(self.concurrency)
        self._json_schema_ok = True
        self._client = None
        self._mock: list[dict] | None = None

    # ------------------------------------------------------------------ общий вход
    def cache_key(self, messages: list[dict]) -> str:
        h = hashlib.sha256()
        h.update(self.model.encode())
        h.update(PROMPT_VERSION.encode())
        for m in messages:
            h.update(m["role"].encode())
            h.update(m["content"].encode("utf-8"))
        return h.hexdigest()

    def is_cached(self, messages: list[dict]) -> bool:
        return (self.cache_dir / f"{self.cache_key(messages)}.json").exists()

    def extract(self, messages: list[dict]) -> LLMResponse:
        key = self.cache_key(messages)
        path = self.cache_dir / f"{key}.json"
        if self.backend != "mock" and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.usage.add(cached=1)
                return LLMResponse.model_validate(data["response"])
            except (ValueError, KeyError, ValidationError):
                pass
        if self.backend == "mock":
            resp = self._mock_answer(messages)
            self.usage.add(calls=1)
            return resp
        resp, usage = self._call_validated(messages)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model, "prompt_version": PROMPT_VERSION,
                                    "response": resp.model_dump(), "usage": usage}, ensure_ascii=False),
                        encoding="utf-8")
        return resp

    # ------------------------------------------------------------------ mock
    def _mock_answer(self, messages: list[dict]) -> LLMResponse:
        if self._mock is None:
            p = Path(os.environ.get("LLM_MOCK_FILE") or ROOT / "pipeline" / "tests" / "data" / "llm_mock" / "responses.json")
            self._mock = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        user = messages[-1]["content"]
        for item in self._mock:
            if item.get("match") and item["match"] in user:
                return LLMResponse.model_validate({"records": item.get("records", [])})
        return LLMResponse(records=[])

    # ------------------------------------------------------------------ Yandex AI Studio
    def _openai(self):
        if self._client is None:
            key, folder = os.environ.get("YC_API_KEY"), os.environ.get("YC_FOLDER_ID")
            if not key or not folder:
                raise LLMUnavailable("нет YC_API_KEY / YC_FOLDER_ID")
            from openai import OpenAI

            self._client = OpenAI(
                api_key=key, base_url=os.environ.get("LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1"),
                default_headers={"x-folder-id": folder}, max_retries=0, timeout=120,
            )
        return self._client

    def _call_validated(self, messages: list[dict]) -> tuple[LLMResponse, dict]:
        text, usage = self._call(messages)
        try:
            return LLMResponse.model_validate_json(_strip_fences(text)), usage
        except ValidationError as e:
            self.usage.add(retries=1)
            fix = messages + [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": "Ответ не прошёл проверку схемы: " + str(e)[:1500]
                 + "\nВерни исправленный JSON {\"records\": [...]} строго по правилам."},
            ]
            text2, usage2 = self._call(fix)
            usage = {k: usage.get(k, 0) + usage2.get(k, 0) for k in set(usage) | set(usage2)}
            try:
                return LLMResponse.model_validate_json(_strip_fences(text2)), usage
            except ValidationError:
                self.usage.add(errors=1)
                log.warning("ответ LLM не прошёл валидацию после повтора — фрагмент пропущен")
                return LLMResponse(records=[]), usage

    def _call(self, messages: list[dict]) -> tuple[str, dict]:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, BadRequestError, RateLimitError

        client = self._openai()
        delay = 2.0
        for attempt in range(6):
            fmt = ({"type": "json_schema", "json_schema": {"name": "records", "schema": response_json_schema()}}
                   if self._json_schema_ok else {"type": "json_object"})
            try:
                with self._sem:
                    r = client.chat.completions.create(model=self.model, messages=messages, temperature=0,
                                                       response_format=fmt, max_tokens=4000)
                u = r.usage
                usage = {"prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                         "completion_tokens": getattr(u, "completion_tokens", 0) or 0}
                self.usage.add(calls=1, **usage)
                self._log_usage(usage)
                return r.choices[0].message.content or "", usage
            except BadRequestError as e:
                if self._json_schema_ok:
                    log.info("response_format=json_schema не поддержан (%s) — переключаюсь на json_object", e.status_code)
                    self._json_schema_ok = False
                    continue
                raise
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                err = e
            except APIStatusError as e:
                if e.status_code < 500:
                    if e.status_code in (401, 402, 403):
                        raise LLMUnavailable(f"API отказал: HTTP {e.status_code}") from e
                    raise
                err = e
            self.usage.add(retries=1)
            wait = delay * (2 ** attempt) * (0.8 + 0.4 * random.random())
            log.warning("LLM: %s, повтор через %.1f с", type(err).__name__, wait)
            time.sleep(min(wait, 60))
        self.usage.add(errors=1)
        raise LLMUnavailable("LLM не ответил после повторов")

    def _log_usage(self, usage: dict):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with (self.cache_dir / "usage.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": self.model,
                                    "prompt_version": PROMPT_VERSION, **usage}) + "\n")
        except OSError:
            pass


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    i, j = t.find("{"), t.rfind("}")
    return t[i: j + 1] if i >= 0 and j > i else t
