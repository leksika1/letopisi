import argparse
import json
from pathlib import Path
from typing import Any

from .common import FIXTURES, OUT, ROOT, JsonClient, write_csv

URL = "https://nominatim.openstreetmap.org/search"
FIELDS = ["name", "lat", "lon", "source", "found", "id", "coords_source"]


def choose(results: list[dict[str, Any]], category: str) -> dict[str, Any] | None:
    types = {
        "река": {"river", "stream", "waterway"},
        "озеро": {"lake", "water", "reservoir"},
        "остров": {"island", "islet"},
        "гора": {"peak", "hill"},
        "поселок": {"village", "hamlet", "town", "city", "locality"},
        "населённый пункт": {"village", "hamlet", "town", "city", "settlement"},
        "ручей": {"stream"},
        "урочище": {"locality"},
        "болото": {"wetland", "marsh", "bog"},
        "болотный массив": {"wetland", "marsh", "bog"},
        "залив реки Паз": {"bay"},
        "порог/водопад": {"waterfall", "rapids"},
    }
    primary = category.partition(",")[0].partition(" (")[0].strip()
    expected = types.get(primary)
    for row in results:
        lat, lon = float(row["lat"]), float(row["lon"])
        if not (68.9 <= lat <= 69.5 and 28.6 <= lon <= 30.2):
            continue
        if (
            expected
            and row.get("type") not in expected
            and row.get("addresstype") not in expected
        ):
            continue
        return row
    return None


def geocode(
    items: list[dict[str, Any]], client: JsonClient, output: Path, url: str = URL
) -> None:
    places, review = [], []
    for item in items:
        found = choose(
            client.get(
                url,
                q=item["name"],
                format="jsonv2",
                limit=5,
                viewbox="28.6,69.5,30.2,68.9",
                bounded=1,
                **{"accept-language": "ru"},
            ),
            item.get("category") or "",
        )
        row = {
            "id": item.get("id"),
            "name": item["name"],
            "lat": item.get("lat"),
            "lon": item.get("lon"),
            "coords_source": item.get("coords_source"),
            "source": None,
            "found": bool(found),
        }
        if found:
            row.update(
                lat=found["lat"],
                lon=found["lon"],
                coords_source="gazetteer",
                source=f"OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/{found['osm_type']}/{found['osm_id']}",
            )
        else:
            review.append(row)
        places.append(row)
    write_csv(output / "places.csv", FIELDS, places)
    write_csv(output / "review_places.csv", FIELDS, review)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=FIXTURES / "places.json")
    p.add_argument("--output", type=Path, default=OUT)
    p.add_argument("--cache", type=Path, default=ROOT / "data" / "nominatim_cache")
    p.add_argument("--url", default=URL)
    args = p.parse_args()
    geocode(
        json.loads(args.input.read_text(encoding="utf-8")),
        JsonClient(args.cache, 1.05),
        args.output,
        args.url,
    )


if __name__ == "__main__":
    main()
