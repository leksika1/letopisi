"""Publish the evaluator's report and exact per-book metrics for a stand bundle."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.eval.evaluate import evaluate, main, read_gold
from pipeline.normalize.dicts import ROOT


def build(data: Path, gold_path: Path, observations_path: Path) -> None:
    with TemporaryDirectory(dir=data) as temporary:
        stage = Path(temporary)
        output = stage / "quality.json"
        report = main(
            [
                "--gold",
                str(gold_path),
                "--observations",
                str(observations_path),
                "--out",
                str(output),
            ]
        )
        # Only multi-book evaluations need extra reports: field-level counts
        # cannot be recovered from the rounded record-level scores in by_document.
        if len(report["by_document"]) > 1:
            gold = read_gold(gold_path)
            with observations_path.open(encoding="utf-8") as src:
                observations = [json.loads(line) for line in src if line.strip()]
            scopes = {}
            for row in report["by_document"]:
                doc_id = row["document_id"]
                scoped = evaluate(
                    [g for g in gold if g["document_id"] == doc_id],
                    [o for o in observations if o["document_id"] == doc_id],
                )
                del scoped["matches"]
                scopes[str(doc_id)] = {
                    **report,
                    **scoped,
                    "unsupported": [
                        r for r in report["unsupported"] if r["document_id"] == doc_id
                    ],
                }
            details = stage / "quality.documents.json"
            details.write_text(
                json.dumps(
                    {
                        "report_sha256": hashlib.sha256(
                            output.read_bytes()
                        ).hexdigest(),
                        "reports": scopes,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            details.replace(data / details.name)
        output.replace(data / output.name)


if __name__ == "__main__":
    build(
        ROOT / "data",
        ROOT / "gold/sanity_2020.csv",
        ROOT / "data/stand_bundle/observations.jsonl",
    )
