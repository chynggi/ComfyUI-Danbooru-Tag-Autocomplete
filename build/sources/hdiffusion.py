"""HDiffusion/historical-danbooru-tag-counts: daily Danbooru tag counts.

Layout (verified 2026-09-22): files named danbooru-YYYY-MM-DD.csv, no header row.
Columns: tag, category, count, "comma,separated,aliases"
Only tags with count >= 50 are present, and there is no deprecated flag.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from artifact import normalize_tag

from .base import FetchResult, SourceData, TagRecord, download, hf_dataset_revision, hf_dataset_tree, hf_resolve_url


class HDiffusionSource:
    id = "HDiffusion/historical-danbooru-tag-counts"
    filename_pattern = re.compile(r"^danbooru-(\d{4}-\d{2}-\d{2})\.csv$")

    def latest_entry(self, entries: list[dict]) -> tuple[str, str]:
        candidates = []
        for entry in entries:
            match = self.filename_pattern.match(entry.get("path", ""))
            if match:
                candidates.append((match.group(1), entry["path"]))
        if not candidates:
            raise RuntimeError(f"{self.id}: no danbooru-YYYY-MM-DD.csv file found")
        return max(candidates)

    def fetch(self, cache_dir: Path) -> FetchResult:
        revision = hf_dataset_revision(self.id)
        data_date, path = self.latest_entry(hf_dataset_tree(self.id, revision))
        destination = cache_dir / self.id.replace("/", "__") / revision / path
        download(hf_resolve_url(self.id, revision, path), destination)
        return FetchResult(self.id, revision, data_date, {"csv": destination})

    def read(self, fetched: FetchResult) -> SourceData:
        tags = {}
        aliases = {}
        with open(fetched.files["csv"], "r", encoding="utf-8", newline="") as handle:
            for line_number, row in enumerate(csv.reader(handle), 1):
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) < 3:
                    raise ValueError(f"{self.id}:{line_number}: expected at least 3 columns, got {len(row)}")
                name = normalize_tag(row[0])
                if not name:
                    raise ValueError(f"{self.id}:{line_number}: empty tag name")
                try:
                    category = int(row[1])
                    post_count = int(row[2])
                except ValueError as exc:
                    raise ValueError(f"{self.id}:{line_number}: {exc}") from exc
                alias_cell = row[3] if len(row) > 3 else ""
                alias_names = tuple(
                    alias for alias in (normalize_tag(part) for part in alias_cell.split(",")) if alias
                )
                tags[name] = TagRecord(name, category, max(0, post_count), False, alias_names)
                for alias in alias_names:
                    if alias != name:
                        aliases[alias] = name
        return SourceData(self.id, fetched.revision, fetched.data_date, tags, aliases)
