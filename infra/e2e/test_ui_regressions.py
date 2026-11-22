"""Browser regressions for links from exports and review queues larger than one API page."""

import csv
import re
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from urllib.parse import parse_qs, urlsplit

import httpx
from playwright.sync_api import Page, expect
from sqlalchemy import MetaData, Table, create_engine, delete, insert, select


@contextmanager
def additional_flagged_records(database_url: str, count: int) -> Iterator[list[int]]:
    engine = create_engine(database_url)
    ids: list[int] = []
    try:
        with engine.begin() as conn:
            observations = Table("observations", MetaData(), autoload_with=conn)
            # Fixture #2 already has a valid date, source and year_outside_book flag.
            source = dict(
                conn.execute(select(observations).where(observations.c.id == 2))
                .mappings()
                .one()
            )
            assert source["status"] == "auto" and source["flags"]
            del source["id"]
            ids = list(
                conn.scalars(
                    insert(observations).returning(observations.c.id), [source] * count
                )
            )
        yield ids
    finally:
        try:
            if ids:
                with engine.begin() as conn:
                    conn.execute(delete(observations).where(observations.c.id.in_(ids)))
        finally:
            engine.dispose()


def test_review_queue_reaches_beyond_first_api_page(
    page: Page, api: httpx.Client, e2e_database_url: str
) -> None:
    with additional_flagged_records(e2e_database_url, 501):
        filters: dict[str, str | int] = {
            "flagged": "true",
            "status": "auto",
            "sort": "id",
            "order": "asc",
            "page_size": 500,
        }
        first = api.get("/api/observations", params={**filters, "page": 1})
        second = api.get("/api/observations", params={**filters, "page": 2})
        first.raise_for_status()
        second.raise_for_status()
        last_id = first.json()["items"][-1]["id"]
        next_id = second.json()["items"][0]["id"]
        total = first.json()["total"]
        assert total == second.json()["total"] > 500
        page.goto(f"/review?status=auto&obs={last_id}")
        expect(page.get_by_text(f"Запись №{last_id}", exact=True)).to_be_visible()
        queue = page.locator("section").filter(
            has=page.get_by_role("heading", name="На проверку")
        )
        expect(queue.locator("[data-review-id]").first).to_be_visible()
        expect(queue.get_by_text(re.compile(rf"\b{total}\s+запис"))).to_be_visible()
        page.keyboard.press("j")
        expect(page.get_by_text(f"Запись №{next_id}", exact=True)).to_be_visible()


def test_closing_exported_source_returns_to_first_search_page(
    page: Page, api: httpx.Client
) -> None:
    exported = api.get(
        "/api/export", params={"format": "csv", "taxon_id": 13, "years": "2020"}
    )
    exported.raise_for_status()
    rows = csv.DictReader(StringIO(exported.content.decode("utf-8-sig")), delimiter=";")
    link = next(
        row["Источник (ссылка)"]
        for row in rows
        if urlsplit(row["Источник (ссылка)"]).path == "/observations/6"
    )
    url = httpx.URL(link)
    assert (url.scheme, url.host, url.port) == (
        api.base_url.scheme,
        api.base_url.host,
        api.base_url.port,
    )
    assert int(parse_qs(urlsplit(link).query)["page"][0]) > 1
    listing = api.get("/api/observations", params={"page": 1, "page_size": 50})
    listing.raise_for_status()
    data = listing.json()
    assert data["items"]
    pages = (data["total"] + data["page_size"] - 1) // data["page_size"]

    page.goto(link)
    panel = page.get_by_role("complementary", name="Карточка записи")
    expect(panel.get_by_text("Запись №6", exact=True)).to_be_visible()
    panel.get_by_role("button", name="Закрыть").click()
    expect(panel).to_have_count(0)
    expect(page.get_by_text(f"стр. 1 из {pages}", exact=True)).to_be_visible()
    expect(page.get_by_role("table").get_by_role("row").nth(1)).to_be_visible()


def test_editor_can_correct_record_type_to_publication(
    page: Page, api: httpx.Client, e2e_database_url: str
) -> None:
    with additional_flagged_records(e2e_database_url, 1) as ids:
        record_id = ids[0]
        endpoint = f"/api/observations/{record_id}"
        before = api.get(endpoint)
        before.raise_for_status()
        record = before.json()["observation"]
        assert record["record_type"] == "observation"
        title = "Хронология цветения герани в заповеднике"

        page.goto(f"/observations/{record_id}")
        page.locator("header").get_by_placeholder("Фамилия И.О.").fill(
            "Проверяющий E2E"
        )
        panel = page.get_by_role("complementary", name="Карточка записи")
        panel.get_by_role("button", name=re.compile("Исправить")).click()
        panel.locator("label").filter(
            has=page.get_by_text("Тип записи", exact=True)
        ).locator("select").select_option("publication")
        panel.locator("label").filter(
            has=page.get_by_text("Название публикации", exact=True)
        ).locator("input").fill(title)
        panel.locator("label").filter(
            has=page.get_by_text("Год публикации", exact=True)
        ).locator("input").fill(str(record["obs_year"]))
        with page.expect_response(
            lambda r: r.url.endswith(endpoint) and r.request.method == "PATCH"
        ) as saved:
            panel.get_by_role("button", name=re.compile("Сохранить правку")).click()
        response = saved.value
        assert response.status == 200, (
            response.request.post_data_json,
            response.status,
            response.text(),
        )
        stored = api.get(endpoint)
        stored.raise_for_status()
        after = stored.json()["observation"]
        assert (after["record_type"], after["object_type"], after["pub_title"]) == (
            "publication",
            "publication",
            title,
        )
        assert after["pub_year"] == record["obs_year"]
