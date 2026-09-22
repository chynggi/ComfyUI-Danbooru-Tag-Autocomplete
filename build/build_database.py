"""Build the runtime artifact from upstream sources.

Pipeline: fetch -> merge -> resolve aliases -> profile filter -> encode -> write.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact import TagEntry, TagSet, encode  # noqa: E402
from fetch_upstream import SOURCE_ORDER, SOURCES, fetch_all  # noqa: E402
from sources.base import SourceData, TagRecord  # noqa: E402


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    threshold: int
    exclude_categories: frozenset[int]
    exclude_deprecated: bool
    extra_sources: tuple[str, ...]


def load_profile(path: Path) -> Profile:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile(
        name=payload.get("name", "danbooru"),
        threshold=int(payload.get("threshold", 25)),
        exclude_categories=frozenset(int(value) for value in payload.get("exclude_categories", [])),
        exclude_deprecated=bool(payload.get("exclude_deprecated", True)),
        extra_sources=tuple(payload.get("extra_sources", [])),
    )


def merge_sources(source_data: list[SourceData]) -> tuple[dict[str, TagRecord], dict[str, str]]:
    """Merge sources in order; later sources override counts, categories and aliases."""
    tags: dict[str, TagRecord] = {}
    aliases: dict[str, str] = {}
    for data in source_data:
        for name, incoming in data.tags.items():
            existing = tags.get(name)
            if existing is None:
                tags[name] = incoming
                continue
            tags[name] = TagRecord(
                name=name,
                category=incoming.category,
                post_count=incoming.post_count,
                is_deprecated=existing.is_deprecated or incoming.is_deprecated,
                aliases=tuple(dict.fromkeys(existing.aliases + incoming.aliases)),
            )
        for alias, target in data.aliases.items():
            aliases[alias] = target
    return tags, aliases


def resolve_aliases(
    aliases: dict[str, str],
    tags: dict[str, TagRecord],
    max_depth: int = 8,
) -> tuple[dict[str, str], dict[str, int]]:
    """Collapse alias chains to a final canonical tag, dropping broken entries."""
    resolved: dict[str, str] = {}
    dropped = {"self": 0, "cycle": 0, "missing": 0, "deprecated": 0}
    for alias, target in aliases.items():
        if alias == target:
            dropped["self"] += 1
            continue
        seen = {alias}
        current = target
        dead = False
        for _ in range(max_depth):
            if current in seen:
                dropped["cycle"] += 1
                dead = True
                break
            seen.add(current)
            following = aliases.get(current)
            if following is not None and following != current:
                current = following
                continue
            if current not in tags:
                dropped["missing"] += 1
                dead = True
                break
            break
        else:
            dropped["cycle"] += 1
            dead = True
        if dead:
            continue
        if tags[current].is_deprecated:
            dropped["deprecated"] += 1
            continue
        resolved[alias] = current
    return resolved, dropped


def assemble(source_data: list[SourceData], profile: Profile) -> tuple[TagSet, dict]:
    tags, aliases = merge_sources(source_data)
    resolved, dropped = resolve_aliases(aliases, tags)

    kept: dict[str, TagRecord] = {}
    for name, record in tags.items():
        if record.post_count < profile.threshold:
            continue
        if record.category in profile.exclude_categories:
            continue
        if profile.exclude_deprecated and record.is_deprecated:
            continue
        kept[name] = record

    alias_only = 0
    for target in set(resolved.values()):
        if target in kept:
            continue
        record = tags[target]
        if record.category in profile.exclude_categories:
            continue
        kept[target] = record
        alias_only += 1

    valid_aliases = {
        alias: target
        for alias, target in resolved.items()
        if target in kept and alias not in kept
    }

    ordered = sorted(kept.items(), key=lambda item: item[0].encode("utf-8"))
    index_of = {name: index for index, (name, _) in enumerate(ordered)}
    alias_names = sorted(valid_aliases, key=lambda alias: alias.encode("utf-8"))

    tagset = TagSet(
        threshold=profile.threshold,
        tags=tuple(
            TagEntry(name, record.category, record.post_count, record.is_deprecated)
            for name, record in ordered
        ),
        aliases=tuple(alias_names),
        alias_target=tuple(index_of[valid_aliases[alias]] for alias in alias_names),
    )
    stats = {
        "source_tags": len(tags),
        "source_aliases": len(aliases),
        "resolved_aliases": len(resolved),
        "alias_only": alias_only,
        "dropped_aliases": dropped,
        "tags": len(tagset.tags),
        "aliases": len(tagset.aliases),
        "deprecated": sum(1 for entry in tagset.tags if entry.deprecated),
    }
    return tagset, stats


def write_artifacts(
    tagset: TagSet,
    out_dir: Path,
    *,
    data_version: str,
    profile_name: str,
    sources_meta: list[dict],
    built_at: str | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = encode(tagset)
    artifact_path = out_dir / "tags.bin.gz"
    with open(artifact_path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", compresslevel=9, mtime=0) as stream:
            stream.write(raw)

    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    metadata = {
        "format_version": 1,
        "data_version": data_version,
        "built_at": built_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": profile_name,
        "threshold": tagset.threshold,
        "counts": {
            "tags": len(tagset.tags),
            "aliases": len(tagset.aliases),
            "deprecated": sum(1 for entry in tagset.tags if entry.deprecated),
        },
        "sources": sources_meta,
        "artifact": {
            "file": "tags.bin.gz",
            "sha256": digest,
            "size": artifact_path.stat().st_size,
            "raw_size": len(raw),
        },
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the runtime tag artifact")
    parser.add_argument("--profile", default="profiles/danbooru.yaml")
    parser.add_argument("--cache", default="data/raw")
    parser.add_argument("--out", default="generated")
    parser.add_argument("--data-version", default=None)
    args = parser.parse_args(argv)

    profile = load_profile(Path(args.profile))
    source_names = list(SOURCE_ORDER) + [name for name in profile.extra_sources if name not in SOURCE_ORDER]
    fetched = fetch_all(source_names, Path(args.cache))
    source_data = [
        SOURCES[name]().read(result) for name, result in zip(source_names, fetched)
    ]

    tagset, stats = assemble(source_data, profile)
    data_version = args.data_version or max(data.data_date for data in source_data).replace("-", ".")
    sources_meta = [
        {"id": data.source_id, "revision": data.revision, "data_date": data.data_date}
        for data in source_data
    ]
    metadata = write_artifacts(
        tagset, Path(args.out), data_version=data_version, profile_name=profile.name, sources_meta=sources_meta
    )
    print(json.dumps(stats, indent=2))
    print(f"data_version={metadata['data_version']} sha256={metadata['artifact']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
