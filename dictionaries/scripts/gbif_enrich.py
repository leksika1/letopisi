import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .common import FIXTURES, OUT, ROOT, JsonClient, write_csv

BASE = "https://api.gbif.org/v1/species"
TAXON = [
    "id",
    "name_lat",
    "name_ru",
    "rank",
    "genus",
    "family",
    "order",
    "class",
    "group_ru",
    "gbif_key",
]
NAME = ["id", "taxon_id", "name", "lang", "kind"]


def group(match: dict[str, Any], known: str | None) -> str | None:
    cls = match.get("class", "")
    if match.get("kingdom") == "Fungi":
        return (
            "лишайники" if known == "лишайники" or cls == "Lecanoromycetes" else "грибы"
        )
    if match.get("phylum") == "Tracheophyta":
        return "сосудистые растения"
    if match.get("phylum") in {"Bryophyta", "Marchantiophyta", "Anthocerotophyta"}:
        return "мхи"
    groups = {
        "Aves": "птицы",
        "Mammalia": "млекопитающие",
        "Reptilia": "амфибии и рептилии",
        "Amphibia": "амфибии и рептилии",
        "Insecta": "насекомые",
        "Actinopterygii": "рыбы",
        "Chondrichthyes": "рыбы",
        "Petromyzonti": "рыбы",
        "Sarcopterygii": "рыбы",
    }
    if cls in groups:
        return groups[cls]
    if match.get("kingdom") == "Animalia" and match.get("phylum") not in {
        None,
        "Chordata",
    }:
        return "прочие беспозвоночные"
    return known


def enrich(
    source: list[dict[str, Any]],
    client: JsonClient,
    output: Path,
    existing_names: list[dict[str, Any]] | None = None,
) -> None:
    taxa, matches, review = [], [], []
    names: list[dict[str, Any]] = []
    # The core merges rows by ID. Reserve all curated IDs, including names
    # for taxa that are pending manual review.
    curated = {(n["taxon_id"], n["name"]): n for n in existing_names or []}
    next_name_id = max((n["id"] for n in curated.values()), default=0) + 1
    by_key: dict[int, int] = {}
    seen: set[tuple[int, str]] = set()
    for index, item in enumerate(source, 1):
        latin = item["name_lat"]
        match = client.get(BASE + "/match", name=latin)
        meta = {
            "name_lat": latin,
            "matchType": match.get("matchType", "NONE"),
            "confidence": match.get("confidence"),
            "usageKey": match.get("usageKey"),
        }
        matches.append(meta)
        if meta["matchType"] != "EXACT" or not meta["usageKey"]:
            review.append(meta)
            continue
        key = int(match.get("acceptedUsageKey") or match["usageKey"])
        accepted = client.get(f"{BASE}/{key}") if key != match["usageKey"] else match
        group_ru = group(accepted, item.get("group_ru"))
        rank = (accepted.get("rank") or "").lower()
        if not group_ru or rank not in {
            "species",
            "subspecies",
            "genus",
            "family",
            "order",
            "class",
        }:
            review.append(meta)
            continue
        tid = by_key.get(key)
        if tid is None:
            tid = int(item.get("id") or index)
            by_key[key] = tid
            taxa.append(
                {
                    "id": tid,
                    "name_lat": accepted["canonicalName"],
                    "name_ru": item.get("name_ru"),
                    "rank": rank,
                    "group_ru": group_ru,
                    "gbif_key": key,
                    **{
                        field: accepted.get(field)
                        for field in ("genus", "family", "order", "class")
                    },
                }
            )
        variants = [
            (accepted["canonicalName"], "lat", "accepted"),
            (item.get("name_ru"), "ru", "accepted"),
            (latin, "lat", "synonym"),
        ]
        offset = 0
        while True:
            page = client.get(f"{BASE}/{key}/synonyms", offset=offset, limit=100)
            rows = page["results"]
            variants.extend(
                (r["canonicalName"], "lat", "synonym")
                for r in rows
                if r.get("canonicalName")
            )
            if page.get("endOfRecords", len(rows) < 100) or not rows:
                break
            offset += len(rows)
        for name, lang, kind in variants:
            if name and (tid, name) not in seen:
                seen.add((tid, name))
                old = curated.get((tid, name))
                name_id = old["id"] if old else next_name_id
                if not old:
                    next_name_id += 1
                names.append(
                    {
                        "id": name_id,
                        "taxon_id": tid,
                        "name": name,
                        "lang": lang,
                        "kind": kind,
                    }
                )
    names.extend(
        n for key, n in curated.items() if key not in seen and key[0] in by_key.values()
    )
    names.sort(key=lambda row: row["id"])
    write_csv(output / "taxa.csv", TAXON, taxa)
    write_csv(output / "taxon_names.csv", NAME, names)
    fields = ["name_lat", "matchType", "confidence", "usageKey"]
    write_csv(output / "gbif_matches.csv", fields, matches)
    write_csv(output / "review_taxa.csv", fields, review)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path)
    p.add_argument("--output", type=Path, default=OUT)
    p.add_argument("--cache", type=Path, default=ROOT / "data" / "gbif_cache")
    args = p.parse_args()
    known = json.loads((FIXTURES / "taxa.json").read_text(encoding="utf-8"))
    if args.input:
        with args.input.open(encoding="utf-8-sig", newline="") as f:
            source = list(csv.DictReader(f))
        known_ids = {r["name_lat"].casefold(): r["id"] for r in known}
        next_id = (
            max(
                [r["id"] for r in known] + [int(r["id"]) for r in source if r.get("id")]
            )
            + 1
        )
        for row in source:
            if not row.get("id"):
                latin = row["name_lat"].casefold()
                if latin not in known_ids:
                    known_ids[latin] = next_id
                    next_id += 1
                row["id"] = str(known_ids[latin])
    else:
        source = known
        write_csv(
            args.output / "taxa_input.csv",
            ["id", "name_ru", "name_lat", "group_ru"],
            [
                {k: row[k] for k in ("id", "name_ru", "name_lat", "group_ru")}
                for row in source
            ],
        )
    existing_names = json.loads(
        (FIXTURES / "taxon_names.json").read_text(encoding="utf-8")
    )
    enrich(source, JsonClient(args.cache, 0.21), args.output, existing_names)


if __name__ == "__main__":
    main()
