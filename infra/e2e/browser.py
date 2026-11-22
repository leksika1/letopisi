"""A fresh browser context for each journey, optionally retaining TypeScript counters."""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Browser, Page


@contextmanager
def browser_page(browser: Browser, base_url: str) -> Iterator[Page]:
    with browser.new_context(
        base_url=base_url,
        locale="ru-RU",
        viewport={"width": 1440, "height": 1000},
        service_workers="block",
    ) as context:
        output = os.getenv("E2E_UI_MEASUREMENT")
        if output:
            # Real navigations replace the JS realm. Preserve counters before unloading.
            context.add_init_script("""addEventListener('pagehide', () => {
                if (window.__coverage__) sessionStorage.setItem('e2e-coverage-' + performance.timeOrigin,
                    JSON.stringify(window.__coverage__));
            });""")
        page = context.new_page()
        page.set_default_timeout(10000)
        # App data and the map's markers still come from the real API.
        page.route("https://*.tile.openstreetmap.org/**", lambda route: route.abort())
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        try:
            yield page
            assert not errors, errors
        finally:
            if output and page.url.startswith(base_url.rstrip("/") + "/"):
                snapshots = page.evaluate("""() => [
                    ...Object.keys(sessionStorage).filter(k => k.startsWith('e2e-coverage-'))
                        .map(k => JSON.parse(sessionStorage.getItem(k))),
                    window.__coverage__
                ]""")
                assert snapshots[-1], "Expected an instrumented frontend bundle"
                (Path(output) / "counters" / f"{uuid4().hex}.json").write_text(
                    json.dumps(snapshots), encoding="utf-8"
                )
