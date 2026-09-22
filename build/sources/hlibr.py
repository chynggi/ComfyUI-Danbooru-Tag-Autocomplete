"""hlibr/danbooru-tag-metadata-snapshot: full Danbooru tag set plus aliases.

Parquet schema (verified 2026-09-22):
  tags: id, name, category, post_count, is_deprecated, words, source
  tag_aliases: id, antecedent_name, consequent_name, status, reason, source
"""

from __future__ import annotations

import json
from pathlib import Path

from artifact import normalize_tag

from .base import FetchResult, SourceData, TagRecord, download, hf_dataset_revision, hf_resolve_url

TAG_COLUMNS = ("name", "category", "post_count", "is_deprecated")
ALIAS_COLUMNS = ("antecedent_name", "consequent_name", "status")


class HlibrSource:
    id = "hlibr/danbooru-tag-metadata-snapshot"

    def fetch(self, cache_dir: Path) -> FetchResult:
        revision = hf_dataset_revision(self.id)
        target = cache_dir / self.id.replace("/", "__") / revision
        files = {
            name: download(hf_resolve_url(self.id, revision, name), target / name)
            for name in ("tags.parquet", "tag_aliases.parquet", "metadata.json")
        }
        return FetchResult(self.id, revision, self.data_date(files["metadata.json"]), files)

    @staticmethod
    def data_date(metadata_path: Path) -> str:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload["snapshot_built_at"][:10]

    def read(self, fetched: FetchResult) -> SourceData:
        import pyarrow.parquet as parquet

        schema_names = set(parquet.read_schema(fetched.files["tags.parquet"]).names)
        missing = [column for column in TAG_COLUMNS if column not in schema_names]
        if missing:
            raise ValueError(f"{self.id}: tags.parquet is missing columns {missing}")

        tags = {}
        columns = parquet.read_table(fetched.files["tags.parquet"], columns=list(TAG_COLUMNS)).to_pydict()
        for name, category, post_count, is_deprecated in zip(*(columns[column] for column in TAG_COLUMNS)):
            normalized = normalize_tag(name)
            if not normalized:
                continue
            tags[normalized] = TagRecord(
                name=normalized,
                category=int(category),
                post_count=max(0, int(post_count)),
                is_deprecated=bool(is_deprecated),
                aliases=(),
            )

        alias_schema = set(parquet.read_schema(fetched.files["tag_aliases.parquet"]).names)
        missing = [column for column in ALIAS_COLUMNS if column not in alias_schema]
        if missing:
            raise ValueError(f"{self.id}: tag_aliases.parquet is missing columns {missing}")

        aliases = {}
        alias_columns = parquet.read_table(
            fetched.files["tag_aliases.parquet"], columns=list(ALIAS_COLUMNS)
        ).to_pydict()
        for alias, target, status in zip(*(alias_columns[column] for column in ALIAS_COLUMNS)):
            if status != "active":
                continue
            normalized_alias = normalize_tag(alias)
            normalized_target = normalize_tag(target)
            if normalized_alias and normalized_target:
                aliases[normalized_alias] = normalized_target

        return SourceData(self.id, fetched.revision, fetched.data_date, tags, aliases)
