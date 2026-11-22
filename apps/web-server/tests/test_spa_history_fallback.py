"""#1108: client routes load the SPA shell without masking missing assets or APIs."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_WEB_SERVER = Path(__file__).resolve().parents[1]
for _path in (_WEB_SERVER, _WEB_SERVER.parent / "backend"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from server.main import SPAStaticFiles  # noqa: E402


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<html>SPA shell</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    app = FastAPI()
    app.mount("/", SPAStaticFiles(directory=str(tmp_path), html=True))
    return TestClient(app)


@pytest.mark.parametrize("path", ["/p/demo", "/settings"])
def test_client_routes_serve_uncached_shell(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.text == "<html>SPA shell</html>"
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache, must-revalidate"


def test_asset_keeps_immutable_cache(client: TestClient) -> None:
    response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log('app')"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("path", ["/assets/missing.js", "/api/nope", "/missing.ico", "/ws/nope"])
def test_server_paths_and_missing_files_keep_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_unmatched_websocket_closes_cleanly(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/unknown"):
        pass
    assert exc.value.code == 1000
