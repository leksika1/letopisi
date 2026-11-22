"""User journeys through the production React bundle, nginx, FastAPI and PostgreSQL."""

import csv
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf
import pytest
from openpyxl import load_workbook
from playwright.sync_api import Page, expect


@pytest.mark.parametrize(
    ("example", "ids"),
    [
        (1, {34, 35, 36, 37, 38}),
        (2, {19, 23, 24, 25, 26, 27, 28, 29, 37}),
        (3, {31}),
        (4, {1, 2, 3, 5, 6}),
    ],
)
def test_jury_search_examples(page: Page, example: int, ids: set[int]) -> None:
    page.goto("/")
    page.get_by_role("button", name=re.compile(rf"^{example}\. ")).click()
    rows = page.locator("tr[data-observation-id]")
    expect(rows).to_have_count(len(ids))
    actual = rows.evaluate_all(
        "(rows) => rows.map(r => Number(r.dataset.observationId))"
    )
    assert set(actual) == ids


def test_search_save_reopen_and_delete(page: Page) -> None:
    page.goto("/?page_size=25")
    rows = page.locator("tr[data-observation-id]")
    expect(rows.first).to_be_visible()
    first_id = rows.first.get_attribute("data-observation-id")
    page.get_by_role("button", name="Вперёд →").click()
    expect(rows.first).not_to_have_attribute("data-observation-id", first_id or "")
    page.get_by_role("button", name="← Назад").click()
    expect(rows.first).to_have_attribute("data-observation-id", first_id or "")
    page.get_by_label("Запрос обычными словами").fill(
        "Все наблюдения за лосем по хронологии"
    )
    page.get_by_role("button", name="Найти").click()
    expect(page.get_by_text("Понято как:", exact=True)).to_be_visible()
    expect(rows.first).to_be_visible()
    name = f"Лось E2E {uuid4().hex[:8]}"
    page.get_by_role("button", name="☆ Сохранить запрос").click()
    page.get_by_placeholder("Название запроса").fill(name)
    with page.expect_response(
        lambda r: (
            r.request.method == "POST"
            and r.url.endswith("/api/saved-queries")
            and r.status == 201
        )
    ):
        page.get_by_role("button", name="Сохранить", exact=True).click()
    page.get_by_role("navigation").get_by_text("Сохранённые запросы").click()
    item = page.get_by_role("listitem").filter(has=page.get_by_role("link", name=name))
    expect(item).to_be_visible()
    item.get_by_role("link", name="Открыть").click()
    expect(page.get_by_label("Активные фильтры")).to_contain_text(
        re.compile("лос|Alces", re.IGNORECASE)
    )
    expect(rows.first).to_be_visible()
    page.get_by_role("navigation").get_by_text("Сохранённые запросы").click()
    page.once("dialog", lambda dialog: dialog.accept())
    item.get_by_role("button", name="Удалить").click()
    expect(item).to_have_count(0)
    page.reload()
    expect(page.get_by_role("heading", name="Сохранённые запросы")).to_be_visible()
    expect(page.get_by_role("link", name=name)).to_have_count(0)


def test_tabs_suggestions_phenology_and_source(page: Page) -> None:
    page.goto("/")
    for name in ("Автор", "Вид", "Все фильтры", "Место", "Фенология"):
        tab = page.get_by_role("tab", name=name, exact=True)
        tab.click()
        expect(tab).to_have_attribute("aria-selected", "true")
        if name == "Место":
            expect(page.locator(".leaflet-container")).to_be_visible()
    page.get_by_placeholder("лось, Alces, Salix…").fill("Geranium")
    page.get_by_role("option").filter(has_text="Geranium sylvaticum").first.click()
    cell = page.get_by_title(re.compile(r"Открыть запись №")).first
    expect(cell).to_be_visible()
    cell.click()
    card = page.get_by_label("Карточка записи")
    expect(card.get_by_role("heading", name="Источник")).to_be_visible()
    expect(card.locator("mark").first).to_be_visible()
    expect(
        card.get_by_role("link", name=re.compile("Открыть оригинал"))
    ).to_have_attribute("href", re.compile(r"/api/documents/3/file#page="))


def test_real_browser_downloads_match_filtered_results(
    page: Page, tmp_path: Path
) -> None:
    page.goto("/?taxon_id=13&years=2020")
    rows = page.locator("tr[data-observation-id]")
    expect(rows.first).to_be_visible()
    visible = rows.evaluate_all("(rows) => rows.map(r => r.dataset.observationId)")
    for ext, label in (("csv", "CSV"), ("xlsx", "Скачать Excel")):
        with page.expect_download() as dl:
            page.get_by_role("link", name=re.compile(label)).click()
        download = dl.value
        assert download.suggested_filename.endswith("." + ext)
        path = tmp_path / download.suggested_filename
        download.save_as(path)
        if ext == "csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                links = [row[13] for row in list(csv.reader(stream, delimiter=";"))[1:]]
        else:
            ws = load_workbook(BytesIO(path.read_bytes())).active
            links = [row[13].hyperlink.target for row in list(ws.rows)[1:]]
        assert [
            url.split("/observations/")[1].split("?")[0] for url in links
        ] == visible


def test_review_keyboard_and_persistent_edit(page: Page, api: httpx.Client) -> None:
    page.goto("/review")
    queue = page.locator("[data-review-id]")
    expect(queue.nth(1)).to_be_visible()
    second = queue.nth(1).get_attribute("data-review-id")
    page.keyboard.press("j")
    card = page.get_by_label("Карточка записи")
    expect(card).to_contain_text(f"№{second}")
    page.keyboard.press("k")
    expect(card).to_contain_text(f"№{queue.first.get_attribute('data-review-id')}")
    # Observation 6 is already edited in the contract fixtures.
    page.goto("/observations/6")
    page.locator("header").get_by_placeholder("Фамилия И.О.").fill("Проверяющий E2E")
    old = api.get("/api/observations/6").json()["observation"]["note"]
    note = "Проверено через браузер " + uuid4().hex[:8]
    try:
        card.get_by_role("button", name=re.compile("Исправить")).click()
        card.locator("label").filter(
            has=page.get_by_text("Примечание", exact=True)
        ).locator("textarea").fill(note)
        with page.expect_response(
            lambda r: (
                r.request.method == "PATCH"
                and r.url.endswith("/api/observations/6")
                and r.status == 200
            )
        ):
            card.get_by_role("button", name=re.compile("Сохранить правку")).click()
        expect(card.get_by_text(note, exact=True).first).to_be_visible()
        page.reload()
        expect(card.get_by_text(note, exact=True).first).to_be_visible()
        history = api.get("/api/observations/6").json()["edits"]
        assert history[-1]["changes"]["note"] == {"old": old, "new": note}
    finally:
        api.patch(
            "/api/observations/6",
            json={"action": "edit", "editor": "E2E", "fields": {"note": old}},
        ).raise_for_status()


def test_upload_and_reopen_original(page: Page, api: httpx.Client) -> None:
    name = f"Летопись 2020 {uuid4().hex[:8]}"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text(
            (50, 50),
            "A new document containing an observation for this end-to-end browser test.",
        )
        original = pdf.tobytes()
    page.goto("/documents")
    page.locator("input[type=file]").set_input_files(
        {"name": name + ".pdf", "mimeType": "application/pdf", "buffer": original}
    )
    expect(page.get_by_label("Год", exact=True)).to_have_value("2020")
    page.get_by_role("button", name="Загрузить", exact=True).click()
    row = page.get_by_role("row").filter(has_text=name)
    expect(row).to_be_visible()
    docs = api.get("/api/documents").json()
    doc = next(d for d in docs if d["title"] == name)
    assert api.get(f"/api/documents/{doc['id']}/file").content == original
    row.get_by_role("link", name="записи").click()
    expect(page.get_by_label("Активные фильтры")).to_contain_text(name)


def test_quality_screen_renders_metrics(page: Page) -> None:
    page.goto("/")
    page.get_by_role("link", name="Качество", exact=True).click()
    expect(page.get_by_role("heading", name="Качество извлечения")).to_be_visible()
    expect(page.get_by_role("columnheader", name="Precision")).to_be_visible()
    expect(page.get_by_role("heading", name="Примеры ошибок")).to_be_visible()


def test_unknown_route(page: Page) -> None:
    page.goto("/no-such-screen")
    expect(page.get_by_text("Страница не найдена.")).to_be_visible()
