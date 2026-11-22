"""Cross-check the reserve's search, map and review workflows against its published fixtures."""

import json
import re
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, expect

from infra.e2e.test_api_e2e import CONTRACTS, ContractAPI

JURY = json.loads(
    (CONTRACTS / "fixtures/saved_queries.json").read_text(encoding="utf-8")
)
JURY_IDS = {
    1: {34, 35, 36, 37, 38},
    2: {19, 23, 24, 25, 26, 27, 28, 29, 37},
    3: {31},
    4: {1, 2, 3, 5, 6},
}


def query_args(filters: Mapping[str, Any]) -> dict[str, str]:
    return {
        k: ",".join(map(str, v)) if isinstance(v, list) else str(v)
        for k, v in filters.items()
    }


@pytest.mark.parametrize("example", JURY, ids=[f"jury-{r['id']}" for r in JURY])
def test_jury_question_from_text_to_records(
    api: httpx.Client, example: dict[str, Any]
) -> None:
    service = ContractAPI(api)
    parsed = service.call("parseQuery", body={"text": example["text"]}).json()
    assert parsed["explanation"]
    # The search endpoint sorts by ascending date when the parser omits a sort.
    effective = {"sort": "date_from", "order": "asc", **parsed["filters"]}
    for key, expected in example["filters"].items():
        assert effective.get(key) == expected
    if "2022" in example["text"]:
        assert any("2022" in w and "2020" in w for w in parsed["warnings"])
    result = service.call(
        "listObservations", params=query_args(parsed["filters"])
    ).json()
    assert {r["id"] for r in result["items"]} == JURY_IDS[example["id"]]
    assert result["total"] == len(JURY_IDS[example["id"]])
    reference = service.call(
        "listObservations", params=query_args(example["filters"])
    ).json()
    assert [r["id"] for r in result["items"]] == [r["id"] for r in reference["items"]]


def test_unknown_terms_and_ambiguous_genus(api: httpx.Client) -> None:
    service = ContractAPI(api)
    unknown = service.call(
        "parseQuery", body={"text": "Все записи про сохатого в 2020 году"}
    ).json()
    assert unknown["filters"]["years"] == [2020]
    assert any("сохатого" in warning for warning in unknown["warnings"])
    assert "taxon_id" not in unknown["filters"]

    genus = service.call(
        "parseQuery", body={"text": "Цветение берёзы с 2018 по 2020, сначала новые"}
    ).json()["filters"]
    assert genus["taxon"].lower().startswith("берёз") and "taxon_id" not in genus
    assert genus["years"] == [2018, 2019, 2020] and genus["order"] == "desc"
    rows = service.call("listObservations", params=query_args(genus)).json()["items"]
    assert rows and all(
        r["taxon_id"] in {3, 4} and r["phenophase_id"] == 5 for r in rows
    )
    for invalid in ({"text": ""}, {"text": 2020}, {"query": "лось"}):
        assert service.call("parseQuery", 422, body=invalid).json()["error"]["message"]


@pytest.mark.parametrize(
    "day,expected",
    [
        ("1995-03-29", set()),
        ("1995-03-30", {34, 35, 36}),
        ("1995-03-31", {34, 35, 36}),
        ("1995-04-01", set()),
    ],
)
def test_search_intersects_both_boundaries(
    api: httpx.Client, day: str, expected: set[int]
) -> None:
    service = ContractAPI(api)
    rows = service.call(
        "listObservations",
        params={"taxon_id": 18, "place_id": 4, "date_from": day, "date_to": day},
    ).json()["items"]
    assert {r["id"] for r in rows} == expected


@pytest.mark.parametrize(
    "location,place_id", [("горы Каскама", 10), ("озера Каскамаярви", 13)]
)
def test_similar_place_names_remain_distinct(
    api: httpx.Client, location: str, place_id: int
) -> None:
    service = ContractAPI(api)
    parsed = service.call(
        "parseQuery", body={"text": f"Наблюдения лося у {location}"}
    ).json()
    assert (parsed["filters"]["taxon_id"], parsed["filters"]["place_id"]) == (
        16,
        place_id,
    )
    rows = service.call(
        "listObservations", params=query_args(parsed["filters"])
    ).json()["items"]
    assert (26 in {r["id"] for r in rows}) is (place_id == 13)


def test_author_period_chips_sort_and_date_filters(
    page: Page, api: httpx.Client
) -> None:
    page.goto("/")
    years = page.locator('[data-filter="years"]')
    years.fill("1995–1996")
    years.press("Enter")
    page.locator('[data-filter="person"]').fill("Сметанников")
    page.get_by_role("option").filter(
        has=page.get_by_text("Сметанников", exact=True)
    ).click()
    chips = page.get_by_label("Активные фильтры")
    expect(chips).to_contain_text("наблюдатель: Сметанников")
    total = page.get_by_test_id("total")
    expect(total).to_have_text("найдено: 5")
    rows = page.locator("tr[data-observation-id]")
    date_sort = page.get_by_role("columnheader", name=re.compile("Дата")).get_by_role(
        "button"
    )
    for order in ("asc", "desc"):
        date_sort.click()
        expect(chips).to_contain_text("↓" if order == "desc" else "↑")
        wanted = api.get(
            "/api/observations",
            params={
                "years": "1995,1996",
                "person": "Сметанников",
                "sort": "date_from",
                "order": order,
            },
        )
        wanted.raise_for_status()
        ids = [str(r["id"]) for r in wanted.json()["items"]]
        expect(rows.first).to_have_attribute("data-observation-id", ids[0])
        assert (
            rows.evaluate_all("(rows) => rows.map(r => r.dataset.observationId)") == ids
        )

    page.locator('[data-filter="record_type"]').select_option("observation")
    page.locator('[data-filter="date_from"]').fill("1995-03-31")
    page.locator('[data-filter="date_to"]').fill("1995-03-31")
    page.locator('[data-filter="flagged"]').check()
    expect(chips).to_contain_text("только сомнительные")
    expect(total).to_have_text("найдено: 3")
    assert set(
        rows.evaluate_all("(rows) => rows.map(r => Number(r.dataset.observationId))")
    ) == {34, 35, 36}

    chips.get_by_role("button", name="годы наблюдения: 1995, 1996", exact=True).click()
    expect(years).to_be_focused()
    years.fill("1996")
    years.press("Enter")
    expect(total).to_have_text("найдено: 0")
    chips.get_by_role(
        "button", name="Удалить фильтр «годы наблюдения: 1996»", exact=True
    ).click()
    expect(total).to_have_text("найдено: 3")
    expect(years).to_have_value("")
    page.reload()
    expect(total).to_have_text("найдено: 3")
    expect(page.locator('[data-filter="flagged"]')).to_be_checked()


def test_select_map_marker_then_species(page: Page, api: httpx.Client) -> None:
    service = ContractAPI(api)
    path = {"id": 6}
    before = service.call("getObservation", path=path).json()["observation"]
    assert before["status"] == "edited"
    # The seeded observations use places without coordinates. Link one to fixture
    # place 5 (Раякоски) so Leaflet renders a real marker from the public API.
    payload = {"action": "edit", "editor": "E2E"}
    try:
        service.call(
            "patchObservation", path=path, body={**payload, "fields": {"place_id": 5}}
        )
        page.goto("/?years=2020")
        page.get_by_role("tab", name="Место", exact=True).click()
        marker = page.get_by_role("button", name="Раякоски", exact=True)
        marker.hover()
        expect(page.locator(".leaflet-tooltip")).to_contain_text("Раякоски")
        marker.click()
        expect(page.get_by_role("heading", name="Раякоски")).to_be_visible()
        species = page.get_by_test_id("place-species")
        expect(species).to_contain_text("Geranium sylvaticum")
        species.get_by_role("button", name=re.compile("Geranium sylvaticum")).click()
        expect(page.get_by_label("Активные фильтры")).to_contain_text(
            "вид: Герань лесная"
        )
        expect(page.get_by_test_id("total")).to_have_text("найдено: 3")
    finally:
        service.call(
            "patchObservation",
            path=path,
            body={**payload, "fields": {"place_id": before["place_id"]}},
        )


def test_verify_then_reject_persists_review_history(
    page: Page, api: httpx.Client
) -> None:
    path = {"id": 6}
    service = ContractAPI(api)
    before = service.call("getObservation", path=path).json()
    assert before["observation"]["status"] == "edited"
    editor, comment = "Проверяющий E2E", "Исправить после повторной сверки со страницей"
    page.goto("/observations/6")
    page.locator("header").get_by_placeholder("Фамилия И.О.").fill(editor)
    panel = page.get_by_label("Карточка записи")
    try:
        panel.get_by_role("button", name=re.compile("Верно")).click()
        history = panel.get_by_test_id("edit-history")
        expect(history.locator("li").first).to_contain_text("Подтверждено")
        panel.get_by_role("button", name=re.compile("Отклонить")).click()
        panel.get_by_placeholder("Почему запись неверна (необязательно)").fill(comment)
        panel.get_by_role("button", name="Отклонить запись", exact=True).click()
        expect(history.locator("li").first).to_contain_text(comment)
        page.reload()
        expect(
            panel.get_by_test_id("edit-history").locator("li").first
        ).to_contain_text(comment)
        after = service.call("getObservation", path=path).json()
        assert after["observation"]["status"] == "rejected"
        recent = after["edits"][len(before["edits"]) :]
        assert [
            (x["action"], x["status_before"], x["status_after"], x["editor"])
            for x in recent
        ] == [
            ("verify", "edited", "verified", editor),
            ("reject", "verified", "rejected", editor),
        ]
        assert recent[-1]["comment"] == comment
    finally:
        service.call(
            "patchObservation",
            path=path,
            body={"action": "edit", "editor": "E2E", "fields": {}},
        )
