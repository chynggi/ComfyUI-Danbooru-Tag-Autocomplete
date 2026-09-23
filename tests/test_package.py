"""The package entry point must load the way ComfyUI loads it.

__init__.py is executed by ComfyUI's node loader as a package, with PromptServer already
constructed. The other tests import the node's modules directly and deliberately skip it.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

NODE_ROOT = Path(__file__).resolve().parents[1]


class RouteRecorder:
    def __init__(self):
        self.handlers = {}

    def get(self, path):
        def decorator(handler):
            self.handlers[path] = handler
            return handler

        return decorator


@pytest.fixture
def loaded_package(monkeypatch):
    recorder = RouteRecorder()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=recorder))
    monkeypatch.setitem(sys.modules, "server", server)
    monkeypatch.setitem(sys.modules, "folder_paths", types.ModuleType("folder_paths"))
    for name in [key for key in sys.modules if key.startswith("dta_package_probe")]:
        monkeypatch.delitem(sys.modules, name, raising=False)

    spec = importlib.util.spec_from_file_location(
        "dta_package_probe", NODE_ROOT / "__init__.py", submodule_search_locations=[str(NODE_ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["dta_package_probe"] = module
    monkeypatch.setitem(sys.modules, "dta_package_probe", module)
    spec.loader.exec_module(module)
    return module, recorder


def test_entry_point_exposes_what_comfyui_reads(loaded_package):
    module, _ = loaded_package
    assert module.WEB_DIRECTORY == "./web"
    assert sorted(module.NODE_CLASS_MAPPINGS) == ["DanbooruTagSearch"]
    assert list(module.NODE_DISPLAY_NAME_MAPPINGS.values()) == ["Danbooru Tag Search"]


def test_entry_point_registers_the_routes(loaded_package):
    _, recorder = loaded_package
    assert sorted(recorder.handlers) == [
        "/danbooru-tag-autocomplete/custom",
        "/danbooru-tag-autocomplete/db",
        "/danbooru-tag-autocomplete/status",
    ]


def test_web_directory_exists(loaded_package):
    module, _ = loaded_package
    assert (NODE_ROOT / module.WEB_DIRECTORY / "search.js").is_file()
