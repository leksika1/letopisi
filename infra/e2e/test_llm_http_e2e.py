"""Cold and warm pipeline runs against a loopback Chat Completions server."""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from docx import Document

from infra.e2e.test_pipeline_e2e import cli, jsonl, prepare_catalog


def test_llm_http_fallback_validation_and_cache(tmp_path: Path) -> None:
    quote = "В июле на маршруте наблюдали лося."
    source = tmp_path / "field-notes.docx"
    doc = Document()
    doc.add_paragraph(quote)
    doc.save(str(source))
    checkout = tmp_path / "checkout"
    prepare_catalog(checkout, {49: source})
    data = checkout / "data"
    cli(checkout, data, "-m", "pipeline.ingest", str(source), "--document-id", "49")

    requests: list[dict[str, Any]] = []
    record = {
        "chunk_ref": 1,
        "record_type": "observation",
        "object_type": "taxon",
        "object_raw": "лося",
        "quote": quote,
        "date_raw": "В июле",
        "obs_year": 2020,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            if (
                self.headers.get("Authorization") != "Bearer test-local"
                or self.headers.get("x-folder-id") != "test-folder"
            ):
                self.send_error(401)
                return
            if len(requests) == 1:
                status = 400
                reply: dict[str, Any] = {
                    "error": {
                        "message": "json_schema is unsupported",
                        "type": "invalid_request_error",
                    }
                }
            elif len(requests) in (2, 3):
                status = 200
                # First return a record missing the required quote. Only the
                # response to the validation retry supplies a valid record.
                content = {
                    "records": [{"record_type": "observation"}]
                    if len(requests) == 2
                    else [record]
                }
                reply = {
                    "id": "local-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "local-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(content, ensure_ascii=False),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 3,
                        "total_tokens": 11,
                    },
                }
            else:
                self.send_error(400, "Unexpected extra request")
                return
            payload = json.dumps(reply, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(checkout),
            "LETOPISI_DATA_DIR": str(data),
            "LLM_CACHE_DIR": str(data / "llm_cache"),
            "LLM_BACKEND": "yandex",
            "LLM_MODEL": "local-test",
            "LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
            "YC_API_KEY": "test-local",
            "YC_FOLDER_ID": "test-folder",
            "OPENAI_API_KEY": "",
            "OPENAI_ORG_ID": "",
            "OPENAI_PROJECT_ID": "",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }

        def run_core() -> tuple[dict[str, Any], list[dict[str, Any]]]:
            run = subprocess.run(
                [sys.executable, "-m", "pipeline.run", "--document", "49"],
                cwd=checkout,
                env=env,
                capture_output=True,
                encoding="utf-8",
                check=False,
                timeout=45,
            )
            assert run.returncode == 0, run.stderr
            report = json.loads(
                (data / "observations/49.report.json").read_text(encoding="utf-8")
            )
            assert report["llm_error"] is None and report["invalid_dropped"] == 0
            return report, jsonl(data / "observations/49.jsonl")

        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            cold, observations = run_core()
            assert [r["response_format"]["type"] for r in requests] == [
                "json_schema",
                "json_object",
                "json_object",
            ]
            original, fallback, repair = requests
            assert original["messages"] == fallback["messages"]
            assert (
                "records"
                in original["response_format"]["json_schema"]["schema"]["properties"]
            )
            assert quote in original["messages"][-1]["content"]
            assert repair["messages"][:-2] == fallback["messages"]
            assert repair["messages"][-2]["role"] == "assistant"
            assert "проверку схемы" in repair["messages"][-1]["content"]
            assert cold["llm_windows"] == 1
            usage = cold["llm_usage"]
            assert (
                usage["cached"],
                usage["calls"],
                usage["retries"],
                usage["errors"],
            ) == (0, 2, 1, 0)
            assert (usage["prompt_tokens"], usage["completion_tokens"]) == (16, 6)
            (observation,) = observations
            assert observation["quote"] == quote and observation["quote_span"] == [
                0,
                len(quote),
            ]
            assert observation["locator"] == {"para": 0}
            assert (
                observation["taxon_id"] == 16
                and observation["date_from"] == "2020-07-01"
            )

            warm, replay = run_core()
            assert replay == observations
            assert warm["llm_usage"]["cached"] == 1 and warm["llm_usage"]["calls"] == 0
            assert len(requests) == 3
        finally:
            server.shutdown()
            worker.join()
