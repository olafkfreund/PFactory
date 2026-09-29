"""#825: the /api/tasks routers are ordered so each path reaches its handler.

``tasks.router`` and ``execution.router`` share the ``/api/tasks`` prefix, and
Starlette dispatches to the first route that fully matches (path and method).
While ``tasks.router`` came first, its ``GET /{task_id}`` caught ``GET
/running`` and answered 400. Unit tests that mount one router alone cannot see
this, so this resolves routes on the real ``create_app()`` app, as Starlette
does, without sending a request (no auth, no database).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
# create_app() imports routes/changelog, whose client_errors lives in apps/backend.
for _p in (_WEB_SERVER, _WEB_SERVER.parent / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import FastAPI  # noqa: E402
from starlette.routing import Match  # noqa: E402

from server.config import get_settings  # noqa: E402
from server.main import create_app  # noqa: E402
from server.routes import execution, tasks  # noqa: E402


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FastAPI]:
    settings = get_settings()
    saved = (settings.BACKEND_PATH, settings.DISABLE_AUTH)
    settings.BACKEND_PATH = str(tmp_path_factory.mktemp("no-backend"))
    settings.DISABLE_AUTH = False
    try:
        yield create_app()
    finally:
        settings.BACKEND_PATH, settings.DISABLE_AUTH = saved


def _endpoint(app: FastAPI, method: str, path: str) -> Callable[..., Any] | None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "path_params": {},
        "root_path": "",
        "query_string": b"",
        "headers": [],
    }
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            endpoint: Callable[..., Any] | None = getattr(route, "endpoint", None)
            return endpoint
    return None


@pytest.mark.parametrize(
    ("method", "path", "handler"),
    [
        ("GET", "/api/tasks/running", execution.get_running_tasks),
        ("GET", "/api/tasks/p:1", tasks.get_task),
        ("GET", "/api/tasks/p:1/status", execution.get_task_status),
        ("PATCH", "/api/tasks/p:1/status", tasks.update_task_status),
        ("POST", "/api/tasks/create-and-run", execution.create_and_run_task),
    ],
)
def test_each_task_path_reaches_its_handler(
    app: FastAPI, method: str, path: str, handler: Callable[..., Any]
) -> None:
    got = _endpoint(app, method, path)
    assert got is handler, (
        f"{method} {path} is served by {getattr(got, '__qualname__', got)}, "
        f"not {handler.__qualname__}"
    )
