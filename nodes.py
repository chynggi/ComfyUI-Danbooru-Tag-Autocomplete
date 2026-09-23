"""The `Danbooru Tag Search` node."""

from __future__ import annotations

import logging
import re

from . import store
from .artifact import RANK_NAME_PREFIX

log = logging.getLogger(__name__)

CATEGORY_OPTIONS = ("any", "general", "artist", "copyright", "character", "meta")
CATEGORY_VALUES = {"general": 0, "artist": 1, "copyright": 3, "character": 4, "meta": 5}
SORT_OPTIONS = ("relevance", "post_count", "name")
TERM_SEPARATORS = re.compile(r"[,\n;]")


class DanbooruTagSearch:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "query": ("STRING", {"default": "", "multiline": False}),
                "category": (list(CATEGORY_OPTIONS), {"default": "any"}),
                "min_post_count": ("INT", {"default": 0, "min": 0, "max": 100_000_000}),
                "limit": ("INT", {"default": 32, "min": 1, "max": 200}),
                "sort": (list(SORT_OPTIONS), {"default": "relevance"}),
                "exclude_deprecated": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("tags",)
    FUNCTION = "search"
    CATEGORY = "Danbooru"

    def search(self, query, category, min_post_count, limit, sort, exclude_deprecated):
        terms = [term.strip() for term in TERM_SEPARATORS.split(query) if term.strip()]
        if not terms:
            return ("",)

        try:
            index = store.load_index()
        except (FileNotFoundError, OSError, ValueError) as exc:
            log.warning("danbooru-tag-search: tag database is unavailable: %s", exc)
            return ("",)

        categories = None if category == "any" else frozenset({CATEGORY_VALUES[category]})
        matched: dict[str, object] = {}
        for term in terms:
            for hit in index.search(term, limit=limit, categories=categories, exclude_deprecated=exclude_deprecated):
                if hit.post_count < min_post_count:
                    continue
                matched.setdefault(hit.name, hit)

        hits = list(matched.values())
        if sort == "post_count":
            hits.sort(key=lambda hit: (-hit.post_count, hit.name))
        elif sort == "name":
            hits.sort(key=lambda hit: hit.name)
        else:
            hits.sort(key=lambda hit: (hit.rank, hit.name_length if hit.rank == RANK_NAME_PREFIX else 0, -hit.post_count, hit.name))

        return (", ".join(hit.name for hit in hits[:limit]),)
