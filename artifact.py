"""Danbooru tag artifact: binary format and search index.

Runtime module. Imported by the ComfyUI node, by the build scripts, and by tests.
Keep it free of ComfyUI imports so the build pipeline can run standalone.
"""

from __future__ import annotations

import sys
import struct
from dataclasses import dataclass

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

    def to_tagset(self) -> TagSet:
        return TagSet(
            threshold=self.threshold,
            tags=tuple(self.entry(index) for index in range(self.n_tags)),
            aliases=tuple(self.alias(index) for index in range(self.n_aliases)),
            alias_target=tuple(self.alias_target(index) for index in range(self.n_aliases)),
        )

    @classmethod
    def from_tagset(cls, tagset: TagSet) -> "Artifact":
        return cls(encode(tagset))


def decode(data: bytes) -> Artifact:
    return Artifact(data)
