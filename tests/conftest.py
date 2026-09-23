import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "build") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "build"))

import types

import pytest

NODE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def node_package(monkeypatch):
    """Expose the node folder as an importable package without running __init__.py."""
    package = types.ModuleType("dta_node")
    package.__path__ = [str(NODE_ROOT)]
    monkeypatch.setitem(sys.modules, "dta_node", package)
    return "dta_node"
