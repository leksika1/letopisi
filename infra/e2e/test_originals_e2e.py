"""Verify the stand's ZIP importer using the same command invoked by stand_setup.sh."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf

from backend.app.db import ROOT


def test_only_contract_originals_reach_the_stand(tmp_path: Path) -> None:
    folder = tmp_path / "contracts/fixtures"
    folder.mkdir(parents=True)
    tool = ROOT / "infra/import_originals.py"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 50), "Verified original cited by observations")
        original = pdf.tobytes()
    name = "book27_2020.pdf"
    missing = "book02_1995.pdf"
    catalog = [
        {
            "format": "pdf",
            "filename": name,
            "sha256": hashlib.sha256(original).hexdigest(),
        },
        {"format": "pdf", "filename": missing, "sha256": "a" * 64},
    ]
    (folder / "documents.json").write_text(json.dumps(catalog), encoding="utf-8")
    case = tmp_path / "case.zip"
    with ZipFile(case, "w", ZIP_DEFLATED) as archive:
        archive.writestr("case/renamed.pdf", original)
        archive.writestr("2020/appendix.pdf", b"unrelated")
        archive.writestr("book 1995.pdf", b"")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(tool),
                str(case),
                "--root",
                str(tmp_path),
            ],
            cwd=ROOT,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    for _ in range(2):
        result = run()
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [name]
        assert missing in result.stderr
        assert (tmp_path / "data/raw" / name).read_bytes() == original
        assert not (tmp_path / "data/raw" / missing).exists()
    case.unlink()
    assert run().stdout.splitlines() == [name]
    (tmp_path / "data/raw" / name).write_bytes(b"old, damaged original")
    refused = run()
    assert refused.returncode != 0 and name in refused.stderr and not refused.stdout
