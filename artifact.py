"""Danbooru tag artifact: binary format and search index.

Runtime module. Imported by the ComfyUI node, by the build scripts, and by tests.
Keep it free of ComfyUI imports so the build pipeline can run standalone.
"""

from __future__ import annotations

import csv
import heapq
import io
import json
import sys
import struct
from dataclasses import dataclass
from typing import Callable, Iterator

MAGIC = b"DTA1"
FORMAT_VERSION = 1
HEADER_SIZE = 64

FLAG_HAS_ALIASES = 1 << 0

if sys.byteorder != "little":
    raise RuntimeError("the artifact format is little-endian and does not support big-endian hosts")

# magic(4s), format_version(H), flags(H), then 14 uint32 fields -> 64 bytes.
_HEADER = struct.Struct("<4sHH" + "I" * 14)
assert _HEADER.size == HEADER_SIZE


def normalize_tag(name: str) -> str:
    """Danbooru tag names are lowercase with underscores; queries may use spaces."""
    return name.strip().lower().replace(" ", "_")


@dataclass(frozen=True, slots=True)
class TagEntry:
    name: str
    category: int
    post_count: int
    deprecated: bool


@dataclass(frozen=True, slots=True)
class TagSet:
    threshold: int
    tags: tuple[TagEntry, ...]
    aliases: tuple[str, ...]
    alias_target: tuple[int, ...]


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _pack_flag_bits(values) -> bytes:
    buf = bytearray((len(values) + 7) // 8)
    for index, flag in enumerate(values):
        if flag:
            buf[index >> 3] |= 1 << (index & 7)
    return bytes(buf)


def encode(tagset: TagSet) -> bytes:
    tags = tagset.tags
    if len(tagset.aliases) != len(tagset.alias_target):
        raise ValueError("aliases and alias_target must have the same length")
    for index, target in enumerate(tagset.alias_target):
        if not 0 <= target < len(tags):
            raise ValueError(f"alias_target[{index}] out of range: {target}")
    for previous, current in zip(tags, tags[1:]):
        if previous.name.encode("utf-8") >= current.name.encode("utf-8"):
            raise ValueError(f"tags must be sorted and unique: {previous.name!r} >= {current.name!r}")
    for previous, current in zip(tagset.aliases, tagset.aliases[1:]):
        if previous.encode("utf-8") >= current.encode("utf-8"):
            raise ValueError(f"aliases must be sorted and unique: {previous!r} >= {current!r}")

    names = bytearray()
    name_offsets = [0]
    for tag in tags:
        names += tag.name.encode("utf-8")
        name_offsets.append(len(names))

    alias_names = bytearray()
    alias_offsets = [0]
    for alias in tagset.aliases:
        alias_names += alias.encode("utf-8")
        alias_offsets.append(len(alias_names))

    name_offsets_b = struct.pack(f"<{len(name_offsets)}I", *name_offsets)
    alias_offsets_b = struct.pack(f"<{len(alias_offsets)}I", *alias_offsets)
    alias_target_b = struct.pack(f"<{len(tagset.alias_target)}I", *tagset.alias_target)
    category_b = bytes(tag.category for tag in tags)
    post_count_b = struct.pack(f"<{len(tags)}I", *(tag.post_count for tag in tags))
    tag_flags_b = _pack_flag_bits([tag.deprecated for tag in tags])

    offset = HEADER_SIZE
    off_names = offset
    offset = _align4(offset + len(names))
    off_name_offsets = offset
    offset = _align4(offset + len(name_offsets_b))
    off_category = offset
    offset = _align4(offset + len(category_b))
    off_post_count = offset
    offset = _align4(offset + len(post_count_b))
    off_tag_flags = offset
    offset = _align4(offset + len(tag_flags_b))
    off_alias_names = offset
    offset = _align4(offset + len(alias_names))
    off_alias_offsets = offset
    offset = _align4(offset + len(alias_offsets_b))
    off_alias_target = offset
    offset = _align4(offset + len(alias_target_b))

    out = bytearray(offset)
    out[0:HEADER_SIZE] = _HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        FLAG_HAS_ALIASES if tagset.aliases else 0,
        len(tags),
        len(tagset.aliases),
        tagset.threshold,
        off_names,
        off_name_offsets,
        off_category,
        off_post_count,
        off_tag_flags,
        off_alias_names,
        off_alias_offsets,
        off_alias_target,
        len(names),
        len(alias_names),
        0,
    )
    out[off_names:off_names + len(names)] = names
    out[off_name_offsets:off_name_offsets + len(name_offsets_b)] = name_offsets_b
    out[off_category:off_category + len(category_b)] = category_b
    out[off_post_count:off_post_count + len(post_count_b)] = post_count_b
    out[off_tag_flags:off_tag_flags + len(tag_flags_b)] = tag_flags_b
    out[off_alias_names:off_alias_names + len(alias_names)] = alias_names
    out[off_alias_offsets:off_alias_offsets + len(alias_offsets_b)] = alias_offsets_b
    out[off_alias_target:off_alias_target + len(alias_target_b)] = alias_target_b
    return bytes(out)


class Artifact:
    """Zero-copy read-only view over an encoded artifact buffer."""

    __slots__ = (
        "threshold", "n_tags", "n_aliases",
        "_buffer", "_names", "_name_offsets", "_category", "_post_count", "_tag_flags",
        "_alias_names", "_alias_offsets", "_alias_target",
    )

    def __init__(self, data: bytes) -> None:
        if len(data) < HEADER_SIZE:
            raise ValueError("artifact is smaller than the 64-byte header")
        (
            magic, version, _flags, n_tags, n_aliases, threshold,
            off_names, off_name_offsets, off_category, off_post_count, off_tag_flags,
            off_alias_names, off_alias_offsets, off_alias_target,
            len_names, len_alias_names, _reserved,
        ) = _HEADER.unpack_from(data, 0)
        if magic != MAGIC:
            raise ValueError(f"bad magic: {magic!r}")
        if version != FORMAT_VERSION:
            raise ValueError(f"unsupported format_version: {version}")

        for label, offset in (
            ("names", off_names),
            ("name_offsets", off_name_offsets),
            ("category", off_category),
            ("post_count", off_post_count),
            ("tag_flags", off_tag_flags),
            ("alias_names", off_alias_names),
            ("alias_offsets", off_alias_offsets),
            ("alias_target", off_alias_target),
        ):
            if offset % 4:
                raise ValueError(f"section {label} is not 4-byte aligned: {offset}")

        name_offsets_size = 4 * (n_tags + 1)
        alias_offsets_size = 4 * (n_aliases + 1)
        sections = (
            ("names", off_names, len_names),
            ("name_offsets", off_name_offsets, name_offsets_size),
            ("category", off_category, n_tags),
            ("post_count", off_post_count, 4 * n_tags),
            ("tag_flags", off_tag_flags, (n_tags + 7) // 8),
            ("alias_names", off_alias_names, len_alias_names),
            ("alias_offsets", off_alias_offsets, alias_offsets_size),
            ("alias_target", off_alias_target, 4 * n_aliases),
        )
        end = HEADER_SIZE
        for label, start, size in sections:
            if start < end:
                raise ValueError(f"section {label} overlaps a previous section")
            end = start + size
            if end > len(data):
                raise ValueError(f"section {label} is truncated")

        self.threshold = threshold
        self.n_tags = n_tags
        self.n_aliases = n_aliases
        self._buffer = data
        self._names = memoryview(data)[off_names:off_names + len_names]
        self._name_offsets = memoryview(data)[off_name_offsets:off_name_offsets + name_offsets_size].cast("I")
        self._category = memoryview(data)[off_category:off_category + n_tags]
        self._post_count = memoryview(data)[off_post_count:off_post_count + 4 * n_tags].cast("I")
        self._tag_flags = memoryview(data)[off_tag_flags:off_tag_flags + (n_tags + 7) // 8]
        self._alias_names = memoryview(data)[off_alias_names:off_alias_names + len_alias_names]
        self._alias_offsets = memoryview(data)[off_alias_offsets:off_alias_offsets + alias_offsets_size].cast("I")
        self._alias_target = memoryview(data)[off_alias_target:off_alias_target + 4 * n_aliases].cast("I")
        self._validate()

    def _validate(self) -> None:
        previous = 0
        for index in range(self.n_tags + 1):
            current = self._name_offsets[index]
            if current < previous:
                raise ValueError("name_offsets must be non-decreasing")
            previous = current
        if previous != len(self._names):
            raise ValueError("name_offsets must end at the names blob length")

        previous = 0
        for index in range(self.n_aliases + 1):
            current = self._alias_offsets[index]
            if current < previous:
                raise ValueError("alias_offsets must be non-decreasing")
            previous = current
        if previous != len(self._alias_names):
            raise ValueError("alias_offsets must end at the alias blob length")

        for index in range(self.n_aliases):
            if self._alias_target[index] >= self.n_tags:
                raise ValueError("alias_target out of range")

        names = bytes(self._names)
        offsets = self._name_offsets
        previous_name = None
        for index in range(self.n_tags):
            current = names[offsets[index]:offsets[index + 1]]
            if previous_name is not None and previous_name >= current:
                raise ValueError("tag names must be sorted and unique")
            previous_name = current

        alias_names = bytes(self._alias_names)
        alias_offsets = self._alias_offsets
        previous_alias = None
        for index in range(self.n_aliases):
            current = alias_names[alias_offsets[index]:alias_offsets[index + 1]]
            if previous_alias is not None and previous_alias >= current:
                raise ValueError("aliases must be sorted and unique")
            previous_alias = current

    def name_bytes(self, index: int) -> bytes:
        return bytes(self._names[self._name_offsets[index]:self._name_offsets[index + 1]])

    def name(self, index: int) -> str:
        return self.name_bytes(index).decode("utf-8")

    def category(self, index: int) -> int:
        return self._category[index]

    def post_count(self, index: int) -> int:
        return self._post_count[index]

    def deprecated(self, index: int) -> bool:
        return bool(self._tag_flags[index >> 3] & (1 << (index & 7)))

    def alias_bytes(self, index: int) -> bytes:
        return bytes(self._alias_names[self._alias_offsets[index]:self._alias_offsets[index + 1]])

    def alias(self, index: int) -> str:
        return self.alias_bytes(index).decode("utf-8")

    def alias_target(self, index: int) -> int:
        return self._alias_target[index]

    def entry(self, index: int) -> TagEntry:
        return TagEntry(self.name(index), self.category(index), self.post_count(index), self.deprecated(index))

    @staticmethod
    def _lower_bound(blob, offsets, count: int, key: bytes) -> int:
        low, high = 0, count
        while low < high:
            mid = (low + high) // 2
            if bytes(blob[offsets[mid]:offsets[mid + 1]]) < key:
                low = mid + 1
            else:
                high = mid
        return low

    def lower_bound_name(self, key: bytes) -> int:
        return self._lower_bound(self._names, self._name_offsets, self.n_tags, key)

    def lower_bound_alias(self, key: bytes) -> int:
        return self._lower_bound(self._alias_names, self._alias_offsets, self.n_aliases, key)

    def to_tagset(self) -> TagSet:
        return TagSet(
            threshold=self.threshold,
            tags=tuple(self.entry(index) for index in range(self.n_tags)),
            aliases=tuple(self.alias(index) for index in range(self.n_aliases)),
            alias_target=tuple(self.alias_target(index) for index in range(self.n_aliases)),
        )

    def to_bytes(self) -> bytes:
        """Return the encoded artifact, byte-identical to what the encoder produced."""
        return self._buffer

    @classmethod
    def from_tagset(cls, tagset: TagSet) -> "Artifact":
        return cls(encode(tagset))


def decode(data: bytes) -> Artifact:
    return Artifact(data)


RANK_EXACT = 0
RANK_NAME_PREFIX = 1
RANK_ALIAS_PREFIX = 2

_TOKEN_SEPARATORS = ",\n;"


def extract_token(text: str, caret: int) -> str:
    """Return the normalized tag token under the caret, or "" when there is none."""
    caret = max(0, min(caret, len(text)))
    head = text[:caret]
    start = 0
    for index in range(len(head) - 1, -1, -1):
        if head[index] in _TOKEN_SEPARATORS:
            start = index + 1
            break
    return normalize_tag(head[start:])


@dataclass(frozen=True, slots=True)
class SearchHit:
    name: str
    category: int
    post_count: int
    deprecated: bool
    alias: str | None
    rank: int
    name_length: int


def _hit_sort_key(hit: SearchHit) -> tuple[int, int, int, str]:
    return (hit.rank, hit.name_length if hit.rank == RANK_NAME_PREFIX else 0, -hit.post_count, hit.name)


class TagIndex:
    """Search over a main artifact with an optional custom overlay.

    Results are exact. Selection is bounded: the name side keeps only the best
    `limit` candidates while scanning, and only those are decoded into SearchHits.
    Custom entries win over main entries with the same canonical name.
    """

    __slots__ = ("_main", "_custom")

    def __init__(self, main: Artifact, custom: Artifact | None = None) -> None:
        self._main = main
        self._custom = custom

    def _name_candidates(
        self,
        source: Artifact,
        key_bytes: bytes,
        categories: frozenset[int] | None,
        exclude_deprecated: bool,
    ) -> Iterator[tuple[int, int, int, int]]:
        """Yield (rank, name_length, -post_count, index) for filtering name matches.

        The index stands in for the name tie-break: a source's names are stored in
        byte order, so index order and name order agree. Nothing is decoded here.
        """
        for index in range(source.lower_bound_name(key_bytes), source.n_tags):
            name_bytes = source.name_bytes(index)
            if not name_bytes.startswith(key_bytes):
                break
            if exclude_deprecated and source.deprecated(index):
                continue
            if categories is not None and source.category(index) not in categories:
                continue
            rank = RANK_EXACT if name_bytes == key_bytes else RANK_NAME_PREFIX
            yield (rank, len(name_bytes), -source.post_count(index), index)

    def _alias_hits(
        self,
        source: Artifact,
        key_bytes: bytes,
        categories: frozenset[int] | None,
        exclude_deprecated: bool,
    ) -> dict[str, SearchHit]:
        best: dict[str, SearchHit] = {}
        for index in range(source.lower_bound_alias(key_bytes), source.n_aliases):
            alias_bytes = source.alias_bytes(index)
            if not alias_bytes.startswith(key_bytes):
                break
            target = source.alias_target(index)
            if exclude_deprecated and source.deprecated(target):
                continue
            category = source.category(target)
            if categories is not None and category not in categories:
                continue
            name_bytes = source.name_bytes(target)
            hit = SearchHit(
                name_bytes.decode("utf-8"), category, source.post_count(target),
                source.deprecated(target), alias_bytes.decode("utf-8"),
                RANK_ALIAS_PREFIX, len(name_bytes),
            )
            current = best.get(hit.name)
            if current is None or _hit_sort_key(hit) < _hit_sort_key(current):
                best[hit.name] = hit
        return best

    def _search_source(
        self,
        source: Artifact,
        key_bytes: bytes,
        limit: int,
        categories: frozenset[int] | None,
        exclude_deprecated: bool,
    ) -> dict[str, SearchHit]:
        combined: dict[str, SearchHit] = {}
        selected = heapq.nsmallest(
            limit, self._name_candidates(source, key_bytes, categories, exclude_deprecated)
        )
        for rank, name_length, _negative_count, index in selected:
            name = source.name(index)
            combined[name] = SearchHit(
                name, source.category(index), source.post_count(index),
                source.deprecated(index), None, rank, name_length,
            )
        for name, hit in self._alias_hits(source, key_bytes, categories, exclude_deprecated).items():
            current = combined.get(name)
            if current is None or _hit_sort_key(hit) < _hit_sort_key(current):
                combined[name] = hit
        return {hit.name: hit for hit in heapq.nsmallest(limit, combined.values(), key=_hit_sort_key)}

    def search(
        self,
        query: str,
        limit: int = 32,
        categories: frozenset[int] | None = None,
        exclude_deprecated: bool = True,
    ) -> list[SearchHit]:
        """Return the best `limit` matches for `query`, ranked exactly."""
        key = normalize_tag(query)
        if not key:
            return []
        key_bytes = key.encode("utf-8")
        if self._custom is None:
            merged = self._search_source(self._main, key_bytes, limit, categories, exclude_deprecated)
        else:
            # The overlay wins by name even when it ranks lower than the main entry it
            # replaces, so over-select main by the number of overlay names to keep the
            # bounded window exact.
            custom = self._search_source(
                self._custom, key_bytes, self._custom.n_tags + self._custom.n_aliases,
                categories, exclude_deprecated,
            )
            merged = self._search_source(
                self._main, key_bytes, limit + len(custom), categories, exclude_deprecated
            )
            merged.update(custom)
        return sorted(merged.values(), key=_hit_sort_key)[:limit]


VALID_CATEGORIES = frozenset({0, 1, 3, 4, 5})
CATEGORY_NAMES = {"general": 0, "artist": 1, "copyright": 3, "character": 4, "meta": 5}


def parse_category(value: str) -> int:
    """Accept a Danbooru category number or its name. Raises ValueError otherwise."""
    text = value.strip().lower()
    if not text:
        return 0
    if text in CATEGORY_NAMES:
        return CATEGORY_NAMES[text]
    return int(text)


@dataclass(frozen=True, slots=True)
class CustomParseResult:
    tagset: TagSet
    warnings: tuple[str, ...]


def build_custom_tagset(
    rows: list[tuple[str, int, int, tuple[str, ...]]],
    lookup: Callable[[str], TagEntry | None] | None = None,
    threshold: int = 0,
) -> CustomParseResult:
    """Turn parsed custom rows into a TagSet usable as a search overlay.

    Each row declares a name and optionally the canonical tag it maps to:
    an empty target tuple declares a tag, a non-empty one declares the row's
    tag as an alias of the first target. Targets the custom rows do not declare
    are resolved through `lookup`, normally the main artifact.
    """
    warnings: list[str] = []
    tag_rows: dict[str, tuple[int, int]] = {}
    alias_rows: list[tuple[str, tuple[str, ...]]] = []

    for name, category, post_count, targets in rows:
        normalized = normalize_tag(name)
        if not normalized:
            warnings.append("skipped a custom row with an empty tag name")
            continue
        if targets:
            alias_rows.append((normalized, tuple(normalize_tag(target) for target in targets)))
            continue
        if category not in VALID_CATEGORIES:
            warnings.append(f"{normalized}: invalid category {category}, using 0")
            category = 0
        tag_rows[normalized] = (category, max(0, post_count))

    entries: dict[str, TagEntry] = {
        name: TagEntry(name, category, post_count, False)
        for name, (category, post_count) in tag_rows.items()
    }

    resolved: dict[str, str] = {}
    for alias, targets in alias_rows:
        if alias in entries:
            warnings.append(f"{alias}: declared as a tag, ignoring its alias mapping")
            continue
        if len(targets) > 1:
            warnings.append(f"{alias}: only the first alias target is used")
        target = targets[0]
        if alias == target:
            warnings.append(f"{alias}: alias points to itself")
            continue
        if target not in entries:
            external = lookup(target) if lookup is not None else None
            if external is None:
                warnings.append(f"{alias}: unknown alias target {target}")
                continue
            entries[target] = external
        resolved[alias] = target

    ordered = sorted(entries.items(), key=lambda item: item[0].encode("utf-8"))
    index_of = {name: index for index, (name, _) in enumerate(ordered)}
    alias_names = sorted(resolved, key=lambda alias: alias.encode("utf-8"))
    return CustomParseResult(
        tagset=TagSet(
            threshold=threshold,
            tags=tuple(entry for _, entry in ordered),
            aliases=tuple(alias_names),
            alias_target=tuple(index_of[resolved[alias]] for alias in alias_names),
        ),
        warnings=tuple(warnings),
    )


def parse_custom_csv(
    text: str,
    lookup: Callable[[str], TagEntry | None] | None = None,
    threshold: int = 0,
) -> CustomParseResult:
    rows: list[tuple[str, int, int, tuple[str, ...]]] = []
    for line_number, row in enumerate(csv.reader(io.StringIO(text)), 1):
        if not row or not any(cell.strip() for cell in row):
            continue
        if line_number == 1 and row[0].strip().lower() in ("tag", "name"):
            continue
        cells = [cell.strip() for cell in row]
        while len(cells) > 4 and cells[-1] == "":
            cells.pop()
        if len(cells) > 4:
            raise ValueError(f"custom CSV line {line_number}: expected 4 columns, got {len(cells)}")
        cells += [""] * (4 - len(cells))
        name, category, post_count, alias_cell = cells
        try:
            rows.append((
                name,
                parse_category(category),
                int(post_count) if post_count else 0,
                tuple(target.strip() for target in alias_cell.split(",") if target.strip()),
            ))
        except ValueError as exc:
            raise ValueError(f"custom CSV line {line_number}: {exc}") from exc
    return build_custom_tagset(rows, lookup=lookup, threshold=threshold)


def parse_custom_json(
    text: str,
    lookup: Callable[[str], TagEntry | None] | None = None,
    threshold: int = 0,
) -> CustomParseResult:
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError("custom JSON must be a list of objects")
    rows: list[tuple[str, int, int, tuple[str, ...]]] = []
    for position, item in enumerate(payload, 1):
        if not isinstance(item, dict) or "tag" not in item:
            raise ValueError(f"custom JSON entry {position}: expected an object with a 'tag' key")
        try:
            category = parse_category(str(item.get("category", 0)))
            post_count = int(item.get("post_count", 0))
        except ValueError as exc:
            raise ValueError(f"custom JSON entry {position}: {exc}") from exc
        targets = item.get("alias") or []
        if isinstance(targets, str):
            target_tuple = tuple(target.strip() for target in targets.split(",") if target.strip())
        else:
            target_tuple = tuple(str(target).strip() for target in targets if str(target).strip())
        rows.append((str(item["tag"]), category, post_count, target_tuple))
    return build_custom_tagset(rows, lookup=lookup, threshold=threshold)


def load_custom(
    text: str,
    path_hint: str,
    lookup: Callable[[str], TagEntry | None] | None = None,
    threshold: int = 0,
) -> CustomParseResult:
    if path_hint.lower().endswith(".json"):
        return parse_custom_json(text, lookup=lookup, threshold=threshold)
    return parse_custom_csv(text, lookup=lookup, threshold=threshold)


def build_custom_overlay(
    main: Artifact,
    text: str,
    path_hint: str,
) -> tuple[Artifact | None, tuple[str, ...]]:
    """Parse custom tag text into an Artifact overlay for `main`.

    Alias targets that are not defined in the custom file are resolved against
    the main artifact so custom aliases can point at upstream tags.
    Raises ValueError on a hard parse error.
    """
    def lookup(name: str) -> TagEntry | None:
        key = name.encode("utf-8")
        index = main.lower_bound_name(key)
        if index < main.n_tags and main.name_bytes(index) == key:
            return main.entry(index)
        return None

    if not text.strip():
        return None, ()
    result = load_custom(text, path_hint, lookup=lookup)
    if not result.tagset.tags:
        return None, result.warnings
    return Artifact.from_tagset(result.tagset), result.warnings
