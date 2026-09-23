"""Danbooru tag autocomplete for ComfyUI."""

from __future__ import annotations

from .nodes import DanbooruTagSearch

WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {"DanbooruTagSearch": DanbooruTagSearch}
NODE_DISPLAY_NAME_MAPPINGS = {"DanbooruTagSearch": "Danbooru Tag Search"}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

from . import routes  # noqa: E402,F401  registers the HTTP routes on import
