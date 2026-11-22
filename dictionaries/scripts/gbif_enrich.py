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


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class TaxonIds:
    def __init__(
        self,
        known: list[dict[str, Any]],
        names: list[dict[str, Any]],
        source: list[dict[str, Any]],
    ) -> None:
        self.by_id = {int(r["id"]): r for r in known}
        self.by_name = {r["name_lat"].casefold(): int(r["id"]) for r in known}
        self.by_key = {
            int(r["gbif_key"]): self.by_name[r["name_lat"].casefold()]
            for r in known
            if r.get("gbif_key")
        }
        self.aliases: dict[str, set[int]] = {}
        for row in names:
            if row["lang"] == "lat":
                self.aliases.setdefault(row["name"].casefold(), set()).add(
                    int(row["taxon_id"])
                )
        self.reserved = set(self.by_id) | {int(r["id"]) for r in source if r.get("id")}
        self.owner = {tid: key for key, tid in self.by_key.items()}

    def resolve(self, key: int, canonical: str, item: dict[str, Any]) -> int:
        latin = item["name_lat"].casefold()
        tid = (
            self.by_key.get(key)
            or self.by_name.get(canonical.casefold())
            or self.by_name.get(latin)
        )
        if tid is None:
            aliases = self.aliases.get(latin, set())
            if len(aliases) == 1:
                tid = next(iter(aliases))
        if item.get("id"):
            explicit = int(item["id"])
            if (tid is not None and explicit != tid) or (
                tid is None and explicit in self.by_id
            ):
                raise ValueError(f"{canonical}: conflicting taxon id {explicit}")
            tid = explicit
        if tid is None:
            # GBIF keys are stable across input order and independently generated subsets.
            tid = key
            if tid in self.reserved:
                raise ValueError(
                    f"{canonical}: GBIF key {key} is already reserved as a taxon id; "
                    "supply an unused id in the input CSV"
                )
        if tid in self.owner and self.owner[tid] != key:
            raise ValueError(
                f"{canonical}: taxon id {tid} already belongs to GBIF {self.owner[tid]}"
            )
        self.owner[tid] = key
        self.by_key[key] = tid
        self.reserved.add(tid)
        return tid


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
    existing_taxa: list[dict[str, Any]] | None = None,
) -> None:
    taxa, matches, review = [], [], []
    names: list[dict[str, Any]] = []
    # The core merges rows by ID. Reserve all curated IDs, including names
    # for taxa that are pending manual review.
    curated = {(int(n["taxon_id"]), n["name"]): n for n in existing_names or []}
    prior_names = (
        read_csv(output / "taxon_names.csv")
        if (output / "taxon_names.csv").exists()
        else []
    )
    name_ids = {(int(n["taxon_id"]), n["name"]): int(n["id"]) for n in prior_names}
    name_ids.update({key: int(n["id"]) for key, n in curated.items()})
    next_name_id = max(name_ids.values(), default=0) + 1
    prior_taxa = read_csv(output / "taxa.csv") if (output / "taxa.csv").exists() else []
    ids = TaxonIds(prior_taxa + (existing_taxa or []), list(curated.values()), source)
    matched_ids: set[int] = set()
    seen: set[tuple[int, str]] = set()
    # A supplied ID applies to all aliases for that GBIF key.
    for item in sorted(source, key=lambda r: not bool(r.get("id"))):
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
        tid = ids.resolve(key, accepted["canonicalName"], item)
        if tid not in matched_ids:
            matched_ids.add(tid)
            taxa.append(
                {
                    "id": tid,
                    "name_lat": accepted["canonicalName"],
                    "name_ru": item.get("name_ru")
                    or ids.by_id.get(tid, {}).get("name_ru"),
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
                name_id = name_ids.get((tid, name))
                if name_id is None:
                    name_id = next_name_id
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
        n for key, n in curated.items() if key not in seen and key[0] in matched_ids
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
        source = read_csv(args.input)
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
    enrich(source, JsonClient(args.cache, 0.21), args.output, existing_names, known)


if __name__ == "__main__":
    main()
