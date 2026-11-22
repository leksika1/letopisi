import csv
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from .common import Fragment, Stats, row_text


def fragments(path: Path, stats: Stats) -> Iterator[Fragment]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(16384)
        try:
            sep = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            sep = ","
        sheets = {
            path.stem: pd.read_csv(
                path,
                sep=sep,
                header=None,
                dtype=str,
                keep_default_na=False,
                skip_blank_lines=False,
                encoding="utf-8-sig",
            )
        }
    else:
        sheets = pd.read_excel(
            path, sheet_name=None, header=None, dtype=str, keep_default_na=False
        )
    for sheet, frame in sheets.items():
        rows = iter(frame.itertuples(index=False, name=None))
        headers = [str(v).strip() for v in next(rows, ())]
        stats.tables += 1
        for number, cells in enumerate(rows, start=2):
            if text := row_text(headers, [str(v).strip() for v in cells]):
                yield Fragment("table_row", {"sheet": str(sheet), "row": number}, text)
