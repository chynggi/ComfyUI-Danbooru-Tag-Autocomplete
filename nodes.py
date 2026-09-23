"""The `Danbooru Tag Search` node."""

from __future__ import annotations

import logging
import re

from . import store
from .artifact import CATEGORY_NAMES, READ_ERRORS, SearchHit, hit_sort_key

log = logging.getLogger(__name__)

CATEGORY_OPTIONS = ("any",) + tuple(CATEGORY_NAMES)
TERM_SEPARATORS = re.compile(r"[,\n;]")


class DanbooruTagSearch:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "query": ("STRING", {"default": "", "multiline": False}),
                "category": (list(CATEGORY_OPTIONS), {"default": "any"}),
                "limit": ("INT", {"default": 32, "min": 1, "max": 200}),
                "exclude_deprecated": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("tags",)
    FUNCTION = "search"
    CATEGORY = "Danbooru"

    def search(self, query, category, limit, exclude_deprecated):
        terms = [term.strip() for term in TERM_SEPARATORS.split(query) if term.strip()]
        if not terms:
            return ("",)

        try:
            index = store.load_index()
        except (*READ_ERRORS, ValueError) as exc:
            log.warning("danbooru-tag-search: tag database is unavailable: %s", exc)
            return ("",)

        categories = None if category == "any" else frozenset({CATEGORY_NAMES[category]})
        matched: dict[str, SearchHit] = {}
        for term in terms:
            for hit in index.search(term, limit=limit, categories=categories, exclude_deprecated=exclude_deprecated):
                current = matched.get(hit.name)
                if current is None or hit_sort_key(hit) < hit_sort_key(current):
                    matched[hit.name] = hit

        hits = sorted(matched.values(), key=hit_sort_key)[:limit]
        return (", ".join(hit.name for hit in hits),)
