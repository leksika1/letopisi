"""Run the built application behind nginx against a disposable PostgreSQL database."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from sqlalchemy import create_engine, make_url

from backend.app.db import LOCAL_DATABASE_URL, ROOT
from infra.e2e.browser import browser_page


@contextmanager
def temporary_database() -> Iterator[str]:
    address = make_url(os.getenv("TEST_DATABASE_URL") or LOCAL_DATABASE_URL)
    if address.drivername == "postgresql":
        address = address.set(drivername="postgresql+psycopg")
    admin = create_engine(address, isolation_level="AUTOCOMMIT")
    name = "letopisi_e2e_" + uuid4().hex
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
            try:
                yield address.set(database=name).render_as_string(hide_password=False)
            finally:
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
    finally:
        admin.dispose()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running(
    args: list[str], cwd: Path, env: dict[str, str], log: Path, port: int
) -> Iterator[httpx.Client]:
    with log.open("wb") as output:
        # A separate Windows process group lets Uvicorn finish requests and flush logs.
        win_server = os.name == "nt" and args[1:3] == ["-m", "infra.e2e.server"]
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if win_server else 0,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20
            ) as client:
                deadline = time.monotonic() + 45
                while proc.poll() is None and time.monotonic() < deadline:
                    try:
                        r = client.get("/api/health", timeout=1)
                        if r.status_code == 200 and r.json().get("db") is True:
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(0.15)
                else:
                    raise RuntimeError(f"Service did not become healthy. See {log}")
                yield client
        finally:
            if proc.poll() is None:
                if win_server:
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except OSError:
                        proc.terminate()
                else:
                    proc.terminate()
                try:
                    proc.wait(8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


@contextmanager
def backend(db_url: str, directory: Path, *, seed: bool) -> Iterator[httpx.Client]:
    data = directory / "data"
    data.mkdir()
    env = {
        **os.environ,
        "DATABASE_URL": db_url,
        "DATA_DIR": str(data),
        "MAX_UPLOAD_BYTES": str(1024 * 1024),
        "LLM_BACKEND": "mock",
        "PYTHONUTF8": "1",
        "YC_API_KEY": "",
        "YC_FOLDER_ID": "",
        "OPENAI_API_KEY": "",
    }
    port = free_port()
    args = [sys.executable, "-m", "infra.e2e.server", str(port)]
    with running(args, ROOT, env, directory / "uvicorn.log", port) as service:
        if seed:
            for index in range(2):
                cmd = subprocess.run(
                    [sys.executable, "pipeline/load.py"],
                    cwd=ROOT,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                counts = json.loads(cmd.stdout)
                if index == 0:
                    assert counts["observations"] > 0
                else:
                    assert all(count == 0 for count in counts.values()), counts
        yield service


@pytest.fixture(scope="session")
def e2e_database_url() -> Iterator[str]:
    with temporary_database() as url:
        yield url


@pytest.fixture
def pipeline_service(tmp_path: Path) -> Iterator[tuple[httpx.Client, str, Path]]:
    with (
        temporary_database() as url,
        backend(url, tmp_path, seed=False) as service,
        gateway(service, tmp_path / "proxy") as proxy,
    ):
        yield proxy, url, tmp_path / "data"


@contextmanager
def gateway(service: httpx.Client, prefix: Path) -> Iterator[httpx.Client]:
    prefix.mkdir()
    executable = os.getenv("NGINX_BIN") or shutil.which("nginx")
    if not executable:
        pytest.fail("Install nginx or set NGINX_BIN to its executable")
    dist = ROOT / "frontend/dist"
    if measurement := os.getenv("E2E_UI_MEASUREMENT"):
        dist = Path(measurement) / "dist"
    if not (dist / "index.html").is_file():
        pytest.fail(
            "Build the frontend using npm ci and npm run build before running E2E"
        )
    shutil.copytree(dist, prefix / "html")
    (prefix / "logs").mkdir()
    (prefix / "temp").mkdir()
    port = free_port()
    conf = (ROOT / "infra/nginx.conf").read_text(encoding="utf-8")
    conf = conf.replace("listen 80;", f"listen 127.0.0.1:{port};")
    conf = conf.replace("root /srv/frontend;", "root html;")
    conf = conf.replace("backend:8000", f"127.0.0.1:{service.base_url.port}")
    # The container's nginx.conf normally supplies the worker, http and MIME settings.
    (prefix / "nginx.conf").write_text(
        "daemon off; master_process off; pid logs/nginx.pid; error_log stderr; events {} http { "
        "access_log off; client_body_temp_path temp/body; proxy_temp_path temp/proxy; "
        "fastcgi_temp_path temp/fastcgi; uwsgi_temp_path temp/uwsgi; scgi_temp_path temp/scgi; "
        "types { text/html html; application/javascript js; text/css css; image/png png; image/svg+xml svg; }\n"
        + conf
        + "\n}",
        encoding="utf-8",
    )
    with running(
        [executable, "-p", "./", "-e", "stderr", "-c", "nginx.conf"],
        prefix,
        dict(os.environ),
        prefix / "nginx-start.log",
        port,
    ) as proxy:
        yield proxy


@pytest.fixture(scope="session")
def api(
    e2e_database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[httpx.Client]:
    directory = tmp_path_factory.mktemp("e2e-web")
    with (
        backend(e2e_database_url, directory, seed=True) as service,
        gateway(service, directory / "proxy") as proxy,
    ):
        yield proxy


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            executable_path=os.getenv("E2E_CHROMIUM") or None
        )
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def page(browser: Browser, api: httpx.Client) -> Iterator[Page]:
    with browser_page(browser, str(api.base_url)) as tab:
        yield tab
