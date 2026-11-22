import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from . import ingest


def main() -> None:
    parser = argparse.ArgumentParser(description="Извлечь фрагменты документа летописи")
    parser.add_argument("file", type=Path)
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "data"))
    )
    args = parser.parse_args()
    print(
        json.dumps(
            asdict(ingest(args.file, args.document_id, args.data_dir)),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
