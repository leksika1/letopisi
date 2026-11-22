from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PDF_2020 = ROOT / "data" / "raw" / "book27_2020.pdf"

needs_pdf = pytest.mark.skipif(not PDF_2020.exists(), reason="нет data/raw/book27_2020.pdf")


@pytest.fixture(scope="session")
def chunks_69_70():
    """Chunks страниц 69–70 книги 2020 (временный дампер)."""
    if not PDF_2020.exists():
        pytest.skip("нет data/raw/book27_2020.pdf")
    from pipeline.devtools.quick_chunks import dump

    return dump(PDF_2020, 3, (69, 70))
