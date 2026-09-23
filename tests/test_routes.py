import importlib
import json
import sys
import types

import pytest

from artifact import TagEntry, TagSet, encode


class RouteRecorder:
    def __init__(self):
        self.handlers = {}

    def _register(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler

        return decorator

    def get(self, path):
        return self._register("GET", path)


class FakeRequest:
    pass


@pytest.fixture
def routes(node_package, tmp_path, monkeypatch):
    recorder = RouteRecorder()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=recorder))
    monkeypatch.setitem(sys.modules, "server", server)
    store_module = importlib.import_module(f"{node_package}.store")
    monkeypatch.setattr(store_module, "cache_dir", lambda: tmp_path)
    monkeypatch.delitem(sys.modules, f"{node_package}.routes", raising=False)
    module = importlib.import_module(f"{node_package}.routes")
    return module, recorder


def build_source(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tags.bin.gz"
    import gzip

    raw = encode(TagSet(threshold=25, tags=(TagEntry("1girl", 0, 10, False),), aliases=(), alias_target=()))
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(raw)
    (directory / "metadata.json").write_text(json.dumps({"data_version": "2026.09.22"}), encoding="utf-8")
    return path


def test_all_routes_are_registered(routes):
    _, recorder = routes
    assert set(recorder.handlers) == {
        ("GET", "/danbooru-tag-autocomplete/status"),
        ("GET", "/danbooru-tag-autocomplete/db"),
        ("GET", "/danbooru-tag-autocomplete/custom"),
    }


def test_status_reports_ready_with_a_local_artifact(routes, tmp_path, monkeypatch):
    module, recorder = routes
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_source(tmp_path / "generated")))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/status")](FakeRequest())
    )

    assert response.status == 200
    assert json.loads(response.body) == {"state": "ready", "dataVersion": "2026.09.22", "error": None}


def test_db_serves_the_artifact_with_gzip_encoding(routes, tmp_path, monkeypatch):
    module, recorder = routes
    source = build_source(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/db")](FakeRequest())
    )

    assert response.status == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert response.headers["Cache-Control"] == "no-cache"


def test_db_returns_404_without_an_artifact(routes, tmp_path, monkeypatch):
    module, recorder = routes
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(tmp_path / "generated" / "tags.bin.gz"))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/db")](FakeRequest())
    )

    assert response.status == 404
    assert "error" in json.loads(response.body)


def test_custom_returns_the_overlay_payload(routes, tmp_path, monkeypatch):
    module, recorder = routes
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_source(tmp_path / "generated")))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/custom")](FakeRequest())
    )

    assert response.status == 200
    assert json.loads(response.body) == {"available": False, "warnings": [], "bytes": None}
