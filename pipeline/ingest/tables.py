import csv
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from .common import Fragment, Stats, row_text


def fragments(path: Path, stats: Stats) -> Iterator[Fragment]:
    leading_rows = 0
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for first in stream:
                if first.strip():
                    break
                leading_rows += 1
            else:
                return
            sample = first + stream.read(16384)
            sample = "\n".join(line for line in sample.splitlines() if line.strip())
        try:
            sep = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            # Missing trailing cells make Sniffer reject otherwise valid CSVs.
            # Use the header's column count, respecting quoted delimiters.
            sep = max(",;\t", key=lambda d: len(next(csv.reader([first], delimiter=d))))
        sheets = {
            path.stem: pd.read_csv(
                path,
                sep=sep,
                header=None,
                dtype=str,
                keep_default_na=False,
                skiprows=leading_rows,
                skip_blank_lines=False,
                encoding="utf-8-sig",
            )
        }
    else:
        sheets = pd.read_excel(
            path, sheet_name=None, header=None, dtype=str, keep_default_na=False
        )
    for sheet, frame in sheets.items():
        rows = enumerate(
            frame.itertuples(index=False, name=None), start=leading_rows + 1
        )
        for _, cells in rows:
            headers = [str(v).strip() for v in cells]
            if any(headers):
                break
        else:
            continue
        stats.tables += 1
        for number, cells in rows:
            if text := row_text(headers, [str(v).strip() for v in cells]):
                yield Fragment("table_row", {"sheet": str(sheet), "row": number}, text)
