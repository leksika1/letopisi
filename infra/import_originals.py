"""List verified PDF originals for the stand, optionally importing a case ZIP."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def digest(stream: IO[bytes]) -> str:
    sha = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        sha.update(block)
    return sha.hexdigest()


def collect(archive: Path, destination: Path, hashes: dict[str, str]) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    ready: set[str] = set()
    for name, sha in hashes.items():
        path = destination / name
        if path.is_file():
            with path.open("rb") as source:
                if digest(source) == sha:
                    ready.add(name)
    pending = {sha: name for name, sha in hashes.items() if name not in ready}
    if archive.is_file() and pending:
        with ZipFile(archive) as z, TemporaryDirectory(dir=destination) as staging:
            for member in z.infolist():
                if not member.file_size or not member.filename.lower().endswith(".pdf"):
                    continue
                with z.open(member) as stream:
                    sha = digest(stream)
                if matched := pending.pop(sha, None):
                    # The target name comes from the contract, independent of ZIP member paths.
                    target = Path(staging) / matched
                    with z.open(member) as stream, target.open("wb") as out:
                        shutil.copyfileobj(stream, out)
                    target.replace(destination / matched)
                    ready.add(matched)
                    if not pending:
                        break
    for name in pending.values():
        if (destination / name).exists():
            raise ValueError(
                f"Неверный SHA-256 у {destination / name}. Нужен оригинал из контракта."
            )
        print(f"Отсутствует проверенный оригинал {name}.", file=sys.stderr)
    return sorted(ready)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Directory containing contracts/ and data/.",
    )
    args = parser.parse_args()
    catalog = json.loads(
        (args.root / "contracts/fixtures/documents.json").read_text(encoding="utf-8")
    )
    files = {
        doc["filename"]: doc["sha256"]
        for doc in catalog
        if doc["format"] == "pdf" and doc["sha256"]
    }
    for name in collect(args.archive, args.root / "data/raw", files):
        print(name)


if __name__ == "__main__":
    main()
