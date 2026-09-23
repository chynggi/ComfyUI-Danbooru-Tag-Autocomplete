import base64
import functools
import gzip
import hashlib
import http.server
import importlib
import json
import threading
import time
from pathlib import Path

import pytest

from artifact import TagEntry, TagSet, encode


def build_artifact(directory: Path) -> Path:
    tagset = TagSet(
        threshold=25,
        tags=(
            TagEntry("1girl", 0, 5000, False),
            TagEntry("blue_hair", 0, 1200, False),
            TagEntry("hatsune_miku", 4, 900, False),
        ),
        aliases=("blu_hair",),
        alias_target=(1,),
    )
    directory.mkdir(parents=True, exist_ok=True)
    raw = encode(tagset)
    path = directory / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(raw)
    metadata = {
        "format_version": 1,
        "data_version": "2026.09.22",
        "profile": "danbooru",
        "threshold": 25,
        "counts": {"tags": 3, "aliases": 1, "deprecated": 0},
        "sources": [],
        "artifact": {
            "file": "tags.bin.gz",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
            "raw_size": len(raw),
        },
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return path


@pytest.fixture
def store(node_package, tmp_path, monkeypatch):
    module = importlib.import_module(f"{node_package}.store")
    monkeypatch.setattr(module, "_state", module.STATE_MISSING)
    monkeypatch.setattr(module, "_error", None)
    monkeypatch.setattr(module, "_download_thread", None)
    monkeypatch.setattr(module, "_artifact_cache", None)
    monkeypatch.setattr(module, "_custom_cache", None)
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(module, "cache_dir", lambda: cache)
    return module


@pytest.fixture
def http_files(tmp_path):
    root = tmp_path / "served"
    root.mkdir()

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def wait_for_state(store, expected, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.status().state == expected:
            return store.status()
        time.sleep(0.02)
    raise AssertionError(f"state never became {expected!r}; last was {store.status()!r}")


def test_local_artifact_override_is_ready_without_network(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))

    assert store.artifact_path() == source
    assert store.status().state == store.STATE_READY
    assert store.status().data_version == "2026.09.22"

    store.ensure_download()
    assert store.status().state == store.STATE_READY


def test_load_index_searches_the_local_artifact(store, tmp_path, monkeypatch):
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_artifact(tmp_path / "generated")))
    index = store.load_index()
    assert [hit.name for hit in index.search("blue_h")] == ["blue_hair"]
    assert [hit.name for hit in index.search("blu_h")] == ["blue_hair"]
    assert store.load_artifact() is store.load_artifact()


def test_status_is_missing_with_an_empty_cache(store):
    status = store.status()
    assert status.state == store.STATE_MISSING
    assert status.data_version is None
    assert status.error is None


def test_download_populates_the_cache_and_verifies_the_sha256(store, tmp_path, http_files, monkeypatch):
    root, base_url = http_files
    source = build_artifact(root)
    pointer = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    (root / "latest.json").write_text(
        json.dumps({
            "data_version": "2026.09.22",
            "profile": "danbooru",
            "sha256": pointer["artifact"]["sha256"],
            "size": pointer["artifact"]["size"],
            "url": f"{base_url}/tags.bin.gz",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DTA_LATEST_URL", f"{base_url}/latest.json")

    store.ensure_download()
    status = wait_for_state(store, store.STATE_READY)

    assert status.data_version == "2026.09.22"
    assert store.artifact_path().read_bytes() == (root / "tags.bin.gz").read_bytes()
    assert store.metadata_path().exists()
    assert store.load_index().search("blue_h")[0].name == "blue_hair"


def test_download_records_an_error_and_does_not_retry(store, tmp_path, http_files, monkeypatch):
    root, base_url = http_files
    source = build_artifact(root)
    (root / "latest.json").write_text(
        json.dumps({
            "data_version": "2026.09.22",
            "profile": "danbooru",
            "sha256": "0" * 64,
            "size": source.stat().st_size,
            "url": f"{base_url}/tags.bin.gz",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DTA_LATEST_URL", f"{base_url}/latest.json")

    store.ensure_download()
    status = wait_for_state(store, store.STATE_ERROR)
    assert "sha256 mismatch" in status.error
    assert not store.artifact_path().exists()

    store.ensure_download()
    assert store.status().state == store.STATE_ERROR


def test_download_failure_without_a_pointer_is_recorded_as_an_error(store, monkeypatch):
    monkeypatch.setenv("DTA_LATEST_URL", "http://127.0.0.1:1/latest.json")
    store.ensure_download()
    status = wait_for_state(store, store.STATE_ERROR)
    assert status.error


def test_artifact_content_encoding_detects_gzip(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    assert store.artifact_content_encoding() == "gzip"

    plain = tmp_path / "generated" / "tags.bin"
    plain.write_bytes(gzip.decompress(source.read_bytes()))
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(plain))
    assert store.artifact_content_encoding() is None


def test_custom_payload_is_absent_without_a_custom_file(store, tmp_path, monkeypatch):
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_artifact(tmp_path / "generated")))
    payload = store.custom_payload()
    assert payload == {"available": False, "warnings": [], "bytes": None}


def test_custom_payload_round_trips_the_overlay(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    (store.cache_dir() / "custom_tags.csv").write_text(
        "example_tag,general,0,\nmy_old_tag,general,0,blue_hair\n", encoding="utf-8"
    )

    payload = store.custom_payload()

    assert payload["available"] is True
    assert payload["warnings"] == []
    overlay_bytes = base64.b64decode(payload["bytes"])
    index = store.load_index()
    assert [hit.name for hit in index.search("my_old_tag")] == ["blue_hair"]
    assert overlay_bytes[:4] == b"DTA1"


def test_custom_payload_reports_a_broken_custom_file(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    (store.cache_dir() / "custom_tags.csv").write_text("a,0,1,x,y\n", encoding="utf-8")

    payload = store.custom_payload()

    assert payload["available"] is False
    assert payload["warnings"]
    assert [hit.name for hit in store.load_index().search("blue_h")] == ["blue_hair"]
