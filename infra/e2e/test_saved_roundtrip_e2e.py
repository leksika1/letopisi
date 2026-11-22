"""A corrected year survives saving, refreshing and browser history navigation."""

import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
from playwright.sync_api import Page, expect


def test_saved_search_preserves_corrected_filters_after_navigation(
    page: Page, api: httpx.Client
) -> None:
    question = "Все наблюдения за лосем в 1995, 1996 и 2022 годах по хронологии"
    name = f"Лось по годам {uuid4().hex[:8]}"
    filters = {
        "taxon_id": 16,
        "years": [1995, 1996, 2020],
        "record_type": "observation",
        "sort": "date_from",
        "order": "asc",
    }
    params = {
        "taxon_id": "16",
        "years": "1995,1996,2020",
        "record_type": "observation",
        "sort": "date_from",
        "order": "asc",
    }
    expected = api.get("/api/observations", params=params)
    expected.raise_for_status()
    expected_ids = [str(r["id"]) for r in expected.json()["items"]]
    assert len(expected_ids) == 9

    def check_results() -> None:
        expect(page.get_by_test_id("total")).to_have_text("найдено: 9")
        assert parse_qs(urlsplit(page.url).query) == {k: [v] for k, v in params.items()}
        expect(page.get_by_label("Активные фильтры")).to_contain_text(
            "годы наблюдения: 1995, 1996, 2020"
        )
        rows = page.locator("tr[data-observation-id]")
        assert (
            rows.evaluate_all("(rs) => rs.map(r => r.dataset.observationId)")
            == expected_ids
        )

    # An explicit year filter is an exact lookup. The natural-language parser
    # corrects the known 2022 typo in this jury question and explains the change.
    page.goto("/?years=2022")
    expect(page.get_by_test_id("total")).to_have_text("найдено: 0")
    page.get_by_label("Запрос обычными словами").fill(question)
    page.get_by_role("button", name="Найти").click()
    expect(page.get_by_text(re.compile("2022 → 2020"))).to_be_visible()
    check_results()
    page.get_by_role("button", name="☆ Сохранить запрос").click()
    page.get_by_placeholder("Название запроса").fill(name)
    with page.expect_response(
        lambda r: r.url.endswith("/api/saved-queries") and r.request.method == "POST"
    ) as received:
        page.get_by_role("button", name="Сохранить", exact=True).click()
    assert received.value.status == 201
    saved = received.value.json()
    try:
        assert saved["filters"] == filters and saved["text"] == question
        page.get_by_role("navigation").get_by_text("Сохранённые запросы").click()
        page.reload()
        item = page.get_by_role("listitem").filter(
            has=page.get_by_role("link", name=name, exact=True)
        )
        expect(item).to_contain_text(question)
        item.get_by_role("link", name="Открыть").click()
        page.reload()
        check_results()
        page.go_back()
        expect(item).to_be_visible()
        page.go_forward()
        check_results()
    finally:
        api.delete(f"/api/saved-queries/{saved['id']}").raise_for_status()
