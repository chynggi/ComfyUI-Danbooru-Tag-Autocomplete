# Danbooru Tag Autocomplete — M1 Data Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the offline data pipeline that turns Hugging Face Danbooru tag metadata into a validated, compact binary runtime artifact (`tags.bin.gz` + `metadata.json`).

**Architecture:** A zero-copy binary artifact (64-byte header, 4-byte aligned sections, little-endian) is produced by a build pipeline that fetches two HF datasets through isolated `SourceAdapter`s, merges them, resolves aliases, applies a profile filter, and validates before writing. The same `artifact.py` module owns the format codec, the Python search index, and custom-tag parsing so the runtime node, the build scripts, and the tests share one definition.

**Tech Stack:** Python 3.13, pytest, pyarrow (build/test only), PyYAML (build only), gzip/stdlib. Development environment via `uv`. No new runtime dependencies.

**Spec:** `docs/specs/2026-09-22-tag-autocomplete-design.md`

## Global Constraints

- Project license: MIT.
- Node runtime dependencies: **0** additional. `requests` ships with ComfyUI at runtime; `pyarrow`, `pyyaml` and `requests` are build/CI/test-only in this project's own venv.
- Python: 3.13 via `uv`. Frontend tests (later milestone) use Node 22.
- Artifact format version **1**, little-endian, header 64 bytes, every section start 4-byte aligned.
- Stored tag names and aliases are always lowercase. Case-insensitivity comes from lowercasing queries, never from the stored data.
- Tag names and aliases sort by **UTF-8 bytes ascending**.
- Repo root is the custom node folder: `custom_nodes/ComfyUI-Danbooru-Tag-Autocomplete`.
- Repo slug: `chynggi/ComfyUI-Danbooru-Tag-Autocomplete`.
- Commit message style: `Add ...`, `Fix ...`, `Use ...`, `Remove ...` (short, imperative).
- Never modify files outside this repo folder.

## File Structure

| File | Responsibility |
|---|---|
| `artifact.py` | Tag data model, binary codec, Python search index, custom-tag parsing |
| `build/sources/base.py` | `TagRecord`, `FetchResult`, `SourceData`, `SourceAdapter` protocol |
| `build/sources/hlibr.py` | Fetch/read `hlibr/danbooru-tag-metadata-snapshot` (parquet) |
| `build/sources/hdiffusion.py` | Fetch/read `HDiffusion/historical-danbooru-tag-counts` (headerless CSV) |
| `build/fetch_upstream.py` | CLI: fetch sources into a local raw cache, write a manifest |
| `build/build_database.py` | Merge, resolve aliases, profile filter, encode, write artifacts |
| `build/validate_database.py` | Validate a built artifact + metadata, exit non-zero on failure |
| `profiles/danbooru.yaml` | Default build profile |
| `tests/conftest.py` | Repo root on `sys.path`, shared fixtures |
| `tests/test_artifact.py` | Codec roundtrip and invariant rejection |
| `tests/test_search.py` | Tokenizer and `TagIndex` ranking |
| `tests/test_sources.py` | hlibr and HDiffusion adapters against local fixtures |
| `tests/test_custom.py` | Custom CSV/JSON parsing and overlays |
| `tests/test_build.py` | Merge priority, alias resolution, profile filtering |
| `tests/test_validate.py` | Each validation rule fires |
| `tests/test_benchmark.py` | 1.71M-tag decode + query latency |
| `tests/fixtures/artifact.bin` | Synthetic artifact shared with the JS search test (M2) |
| `tests/fixtures/queries.json` | Expected search results for the shared fixture (M2) |

---

### Task 1: Project scaffolding + artifact codec

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `tests/conftest.py`
- Create: `artifact.py`
- Create: `tests/test_artifact.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `artifact.MAGIC: bytes`, `artifact.FORMAT_VERSION: int`, `artifact.HEADER_SIZE: int`
  - `artifact.normalize_tag(name: str) -> str`
  - `artifact.TagEntry(name: str, category: int, post_count: int, deprecated: bool)`
  - `artifact.TagSet(threshold: int, tags: tuple[TagEntry, ...], aliases: tuple[str, ...], alias_target: tuple[int, ...])`
  - `artifact.encode(tagset: TagSet) -> bytes`
  - `artifact.Artifact`, constructible as `Artifact(data: bytes)`, with `from_tagset(tagset) -> Artifact`, attributes `threshold`, `n_tags`, `n_aliases`, methods `name(i)`, `name_bytes(i)`, `category(i)`, `post_count(i)`, `deprecated(i)`, `alias(i)`, `alias_bytes(i)`, `alias_target(i)`, `to_tagset()`
  - `artifact.decode(data: bytes) -> Artifact`

- [ ] **Step 1: Create the dev environment and scaffolding**

```bash
cd custom_nodes/ComfyUI-Danbooru-Tag-Autocomplete
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python pytest pyarrow pyyaml requests
mkdir -p build/sources profiles tests/fixtures docs/plans
```

- [ ] **Step 2: Write `pyproject.toml` and `requirements.txt`**

`pyproject.toml`:

```toml
[project]
name = "danbooru-tag-autocomplete"
version = "0.1.0"
description = "Danbooru tag autocomplete for ComfyUI, backed by continuously updated Hugging Face tag metadata"
license = { text = "MIT" }
requires-python = ">=3.10"
dependencies = []

[tool.comfy]
PublisherId = "chynggi"
DisplayName = "Danbooru Tag Autocomplete"
web = "web"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`requirements.txt`:

```
# No runtime dependencies. requests ships with ComfyUI.
# pyarrow and pyyaml are build/CI-only dependencies, listed in .github/workflows/update-data.yml.
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
```

- [ ] **Step 4: Write the failing codec tests**

`tests/test_artifact.py`:

```python
import struct

import pytest

import artifact
from artifact import TagEntry, TagSet, encode, decode


def sample_tagset() -> TagSet:
    return TagSet(
        threshold=25,
        tags=(
            TagEntry("1girl", 0, 100, False),
            TagEntry("blue_hair", 0, 90, False),
            TagEntry("hatsune_miku", 4, 80, True),
        ),
        aliases=("blu_hair", "miku"),
        alias_target=(1, 2),
    )


def test_roundtrip_preserves_every_field():
    original = sample_tagset()
    assert decode(encode(original)).to_tagset() == original


def test_header_layout_is_stable():
    data = encode(sample_tagset())
    assert data[:4] == b"DTA1"
    assert len(data) >= artifact.HEADER_SIZE == 64
    _, version, flags = struct.unpack_from("<4sHH", data, 0)
    assert version == 1
    assert flags & 1


def test_section_offsets_are_4_byte_aligned():
    data = encode(sample_tagset())
    off_names = struct.unpack_from("<I", data, 20)[0]
    assert off_names == 64


def test_deprecated_flag_is_per_tag():
    decoded = decode(encode(sample_tagset()))
    assert [decoded.deprecated(i) for i in range(decoded.n_tags)] == [False, False, True]


def test_encode_rejects_unsorted_tags():
    bad = TagSet(threshold=0, tags=(TagEntry("b", 0, 1, False), TagEntry("a", 0, 1, False)), aliases=(), alias_target=())
    with pytest.raises(ValueError, match="sorted"):
        encode(bad)


def test_encode_rejects_alias_target_out_of_range():
    bad = TagSet(threshold=0, tags=(TagEntry("a", 0, 1, False),), aliases=("b",), alias_target=(1,))
    with pytest.raises(ValueError, match="out of range"):
        encode(bad)


def test_decode_rejects_bad_magic():
    data = bytearray(encode(sample_tagset()))
    data[0:4] = b"XXXX"
    with pytest.raises(ValueError, match="magic"):
        decode(bytes(data))


def test_decode_rejects_unaligned_section():
    data = bytearray(encode(sample_tagset()))
    struct.pack_into("<I", data, 20, 65)
    with pytest.raises(ValueError, match="aligned"):
        decode(bytes(data))


def test_decode_rejects_truncated_buffer():
    data = encode(sample_tagset())
    with pytest.raises(ValueError):
        decode(data[: artifact.HEADER_SIZE + 1])


def test_decode_rejects_unsorted_names():
    data = bytearray(encode(sample_tagset()))
    off_names = struct.unpack_from("<I", data, 20)[0]
    data[off_names:off_names + 5] = b"zzzzz"
    with pytest.raises(ValueError, match="sorted"):
        decode(bytes(data))
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_artifact.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'artifact'`

- [ ] **Step 6: Implement `artifact.py`**

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_artifact.py -v`
Expected: PASS (10 passed)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml requirements.txt artifact.py tests/conftest.py tests/test_artifact.py
git commit -m "Add artifact binary codec"
```

---

### Task 2: Search index and tokenizer

**Files:**
- Modify: `artifact.py` (append search code)
- Create: `tests/test_search.py`
- Create: `tests/fixtures/gen_fixture.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `artifact.TagEntry`, `TagSet`, `Artifact`, `encode`, `decode`, `normalize_tag`
- Produces:
  - `artifact.RANK_EXACT = 0`, `artifact.RANK_NAME_PREFIX = 1`, `artifact.RANK_ALIAS_PREFIX = 2`
  - `artifact.SearchHit(name: str, category: int, post_count: int, deprecated: bool, alias: str | None, rank: int)`
  - `artifact.extract_token(text: str, caret: int) -> str`
  - `artifact.Artifact.lower_bound_name(key: bytes) -> int`, `artifact.Artifact.lower_bound_alias(key: bytes) -> int`
  - `artifact.TagIndex(main: Artifact, custom: Artifact | None = None)` with `.search(query: str, limit: int = 32, categories: frozenset[int] | None = None, exclude_deprecated: bool = True) -> list[SearchHit]`
  - `tests/fixtures/artifact.bin`, `tests/fixtures/queries.json` (consumed by the M2 JS test)

- [ ] **Step 1: Write the failing search tests**

`tests/test_search.py`:

```python
import artifact
from artifact import Artifact, TagEntry, TagIndex, TagSet, extract_token


def build_index(custom: Artifact | None = None) -> TagIndex:
    tagset = TagSet(
        threshold=25,
        tags=(
            TagEntry("1girl", 0, 5000, False),
            TagEntry("blue_hair", 0, 1200, False),
            TagEntry("blue_hair_ornament", 0, 30, False),
            TagEntry("blue_hairband", 0, 82, False),
            TagEntry("hatsune_miku", 4, 900, False),
            TagEntry("old_tag", 0, 10, True),
        ),
        aliases=("blu_hair", "miku", "oldtag"),
        alias_target=(1, 4, 4),
    )
    return TagIndex(Artifact.from_tagset(tagset), custom=custom)


def test_extract_token_stops_at_separators():
    assert extract_token("1girl, blue_h", 13) == "blue_h"
    assert extract_token("1girl, blue_hair, looking_at_", 29) == "looking_at_"
    assert extract_token("blue h", 6) == "blue_h"
    assert extract_token("", 0) == ""


def test_extract_token_uses_caret_not_end_of_text():
    text = "blue_hair, 1girl"
    assert extract_token(text, 4) == "blue"


def test_prefix_search_orders_by_rank_then_length_then_count():
    hits = build_index().search("blue_h")
    assert [hit.name for hit in hits] == ["blue_hair", "blue_hairband", "blue_hair_ornament"]


def test_exact_match_ranks_first():
    hits = build_index().search("blue_hair")
    assert hits[0].rank == artifact.RANK_EXACT
    assert hits[0].name == "blue_hair"


def test_alias_search_resolves_to_canonical():
    hits = build_index().search("blu_h")
    assert hits[0].name == "blue_hair"
    assert hits[0].alias == "blu_hair"
    assert hits[0].rank == artifact.RANK_ALIAS_PREFIX


def test_alias_hit_is_deduplicated_against_name_hit():
    names = [hit.name for hit in build_index().search("miku")]
    assert names.count("hatsune_miku") == 1


def test_deprecated_tags_are_excluded_by_default():
    assert build_index().search("old_tag") == []


def test_deprecated_tags_can_be_included():
    hits = build_index().search("old_tag", exclude_deprecated=False)
    assert hits and hits[0].deprecated is True


def test_category_filter_is_applied():
    assert build_index().search("hatsune_miku", categories=frozenset({0})) == []
    assert build_index().search("hatsune_miku", categories=frozenset({4}))[0].name == "hatsune_miku"


def test_limit_is_respected():
    assert len(build_index().search("blue_h", limit=2)) == 2


def test_empty_and_whitespace_queries_return_nothing():
    assert build_index().search("") == []
    assert build_index().search("   ") == []


def test_custom_entries_win_over_main_entries():
    custom = Artifact.from_tagset(TagSet(
        threshold=0,
        tags=(TagEntry("blue_hair", 4, 99999, False),),
        aliases=(),
        alias_target=(),
    ))
    hits = build_index(custom=custom).search("blue_hair")
    assert hits[0].category == 4
    assert hits[0].post_count == 99999


def test_non_ascii_prefix_search():
    tagset = TagSet(
        threshold=0,
        tags=(TagEntry("蓝发", 0, 5, False), TagEntry("髪", 0, 3, False)),
        aliases=(),
        alias_target=(),
    )
    index = TagIndex(Artifact.from_tagset(tagset))
    assert [hit.name for hit in index.search("蓝")] == ["蓝发"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_search.py -v`
Expected: FAIL with `AttributeError: module 'artifact' has no attribute 'TagIndex'`

- [ ] **Step 3: Append the search implementation to `artifact.py`**

```python
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


def _hit_sort_key(hit: SearchHit) -> tuple[int, int, int, str]:
    name_length = len(hit.name.encode("utf-8")) if hit.rank == RANK_NAME_PREFIX else 0
    return (hit.rank, name_length, -hit.post_count, hit.name)


class TagIndex:
    """Search over a main artifact with an optional custom overlay.

    Custom entries win over main entries with the same canonical name.
    """

    __slots__ = ("_main", "_custom")

    def __init__(self, main: Artifact, custom: Artifact | None = None) -> None:
        self._main = main
        self._custom = custom

    def _collect(self, source: Artifact, key_bytes: bytes) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for index in range(source.lower_bound_name(key_bytes), source.n_tags):
            name_bytes = source.name_bytes(index)
            if not name_bytes.startswith(key_bytes):
                break
            rank = RANK_EXACT if name_bytes == key_bytes else RANK_NAME_PREFIX
            hits.append(SearchHit(
                source.name(index), source.category(index), source.post_count(index),
                source.deprecated(index), None, rank,
            ))
        for index in range(source.lower_bound_alias(key_bytes), source.n_aliases):
            alias_bytes = source.alias_bytes(index)
            if not alias_bytes.startswith(key_bytes):
                break
            target = source.alias_target(index)
            hits.append(SearchHit(
                source.name(target), source.category(target), source.post_count(target),
                source.deprecated(target), source.alias(index), RANK_ALIAS_PREFIX,
            ))
        return hits

    def _search_source(
        self,
        source: Artifact,
        key_bytes: bytes,
        categories: frozenset[int] | None,
        exclude_deprecated: bool,
    ) -> dict[str, SearchHit]:
        best: dict[str, SearchHit] = {}
        for hit in self._collect(source, key_bytes):
            if exclude_deprecated and hit.deprecated:
                continue
            if categories is not None and hit.category not in categories:
                continue
            current = best.get(hit.name)
            if current is None or _hit_sort_key(hit) < _hit_sort_key(current):
                best[hit.name] = hit
        return best

    def search(
        self,
        query: str,
        limit: int = 32,
        categories: frozenset[int] | None = None,
        exclude_deprecated: bool = True,
    ) -> list[SearchHit]:
        """Search the main artifact, then let custom entries replace main entries by name."""
        key = normalize_tag(query)
        if not key:
            return []
        key_bytes = key.encode("utf-8")
        merged = self._search_source(self._main, key_bytes, categories, exclude_deprecated)
        if self._custom is not None:
            merged.update(self._search_source(self._custom, key_bytes, categories, exclude_deprecated))
        return sorted(merged.values(), key=_hit_sort_key)[:limit]
```

- [ ] **Step 4: Add the lower-bound methods to `Artifact`**

Insert these methods into `Artifact`, right after `entry()`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_search.py -v`
Expected: PASS (13 passed)

- [ ] **Step 6: Generate the shared Python/JS fixture**

`tests/fixtures/gen_fixture.py`:

```python
"""Regenerate tests/fixtures/artifact.bin and queries.json.

Run from the repo root: .venv/bin/python tests/fixtures/gen_fixture.py
The M2 JavaScript search test consumes the same files, so the Python and
JavaScript implementations are checked against one expected result set.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from artifact import TagEntry, TagIndex, TagSet, decode, encode  # noqa: E402

TAGSET = TagSet(
    threshold=25,
    tags=(
        TagEntry("1girl", 0, 5000, False),
        TagEntry("blue_hair", 0, 1200, False),
        TagEntry("blue_hair_ornament", 0, 30, False),
        TagEntry("blue_hairband", 0, 82, False),
        TagEntry("hatsune_miku", 4, 900, False),
        TagEntry("highres", 5, 700, False),
        TagEntry("old_tag", 0, 10, True),
    ),
    aliases=("blu_hair", "miku", "oldtag"),
    alias_target=(1, 4, 4),
)

QUERIES = ["blue_h", "blue_hair", "blu_h", "miku", "old_tag", "high", "zzz", ""]

HERE = Path(__file__).resolve().parent
(HERE / "artifact.bin").write_bytes(encode(TAGSET))

index = TagIndex(decode((HERE / "artifact.bin").read_bytes()))
expected = {
    query: [
        {
            "name": hit.name,
            "category": hit.category,
            "postCount": hit.post_count,
            "deprecated": hit.deprecated,
            "alias": hit.alias,
            "rank": hit.rank,
        }
        for hit in index.search(query)
    ]
    for query in QUERIES
}
(HERE / "queries.json").write_text(
    json.dumps({"threshold": TAGSET.threshold, "queries": expected}, indent=2) + "\n",
    encoding="utf-8",
)
print("wrote", HERE / "artifact.bin", "and", HERE / "queries.json")
```

Run: `.venv/bin/python tests/fixtures/gen_fixture.py`
Expected: prints the two fixture paths; `tests/fixtures/artifact.bin` and `tests/fixtures/queries.json` exist.

- [ ] **Step 7: Add a fixture round-trip test**

Append to `tests/test_search.py`:

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_shared_fixture_matches_python_search():
    data = (FIXTURES / "artifact.bin").read_bytes()
    expected = json.loads((FIXTURES / "queries.json").read_text(encoding="utf-8"))
    index = TagIndex(artifact.decode(data))
    for query, hits in expected["queries"].items():
        assert [hit.name for hit in index.search(query)] == [hit["name"] for hit in hits], query
```

- [ ] **Step 8: Run the tests to verify they pass and commit**

Run: `.venv/bin/python -m pytest tests/test_search.py -v`
Expected: PASS (14 passed)

```bash
git add artifact.py tests/test_search.py tests/fixtures
git commit -m "Add tag search index and shared fixture"
```

---

### Task 3: Custom tag parsing

**Files:**
- Modify: `artifact.py` (append custom parsing)
- Create: `tests/test_custom.py`

**Interfaces:**
- Consumes: `normalize_tag`, `TagEntry`, `TagSet`
- Produces:
  - `artifact.VALID_CATEGORIES: frozenset[int]`
  - `artifact.CATEGORY_NAMES: dict[str, int]`
  - `artifact.parse_category(value: str) -> int`
  - `artifact.CustomParseResult(tagset: TagSet, warnings: tuple[str, ...])`
  - `artifact.build_custom_tagset(rows: list[tuple[str, int, int, tuple[str, ...]]], lookup: Callable[[str], TagEntry | None] | None = None, threshold: int = 0) -> CustomParseResult`
  - `artifact.parse_custom_csv(text: str, lookup=None, threshold: int = 0) -> CustomParseResult`
  - `artifact.parse_custom_json(text: str, lookup=None, threshold: int = 0) -> CustomParseResult`
  - `artifact.load_custom(text: str, path_hint: str, lookup=None, threshold: int = 0) -> CustomParseResult`

**Row semantics (the model this task implements):**

A custom row declares a name and, optionally, the canonical tag that name maps to.

- Empty trailing alias column → the row declares a **tag**; `category` and `post_count` are used.
- Non-empty trailing alias column → the row declares an **alias mapping**; the row's `tag` is the alias name (it is *not* also declared as a tag) and the column names the canonical target. Extra targets beyond the first are ignored with a warning.
- `category` accepts either a Danbooru number (`0/1/3/4/5`) or its name (`general/artist/copyright/character/meta`). An out-of-range number is a warning and falls back to `general`; an unparseable value is a hard error.
- A name declared as both a tag and an alias is kept as a tag, and its alias declaration is dropped with a warning.
- An alias target that the custom rows do not declare is resolved through `lookup` (normally the main artifact). If it resolves, the target entry is copied into the overlay so `alias_target` can index it. If it does not resolve, the alias is dropped with a warning.
- Recoverable problems (unknown target, self-reference, out-of-range category, empty name, extra targets) are **warnings**. Malformed input (wrong column count, unparseable number or category, non-list JSON, missing `tag` key) raises `ValueError`.

Worked example that must work verbatim (from the design spec):

```csv
tag,category,post_count,alias
example_tag,general,0,
my_old_tag,general,0,example_tag
```

→ the overlay declares the tag `example_tag` and the alias `my_old_tag → example_tag`.

- [ ] **Step 1: Write the failing custom parsing tests**

`tests/test_custom.py`:

```python
import pytest

from artifact import TagEntry, load_custom, parse_custom_csv, parse_custom_json


def test_empty_alias_column_declares_a_tag():
    result = parse_custom_csv("my_tag,4,120,\n")
    assert [entry.name for entry in result.tagset.tags] == ["my_tag"]
    assert result.tagset.tags[0].category == 4
    assert result.tagset.tags[0].post_count == 120
    assert result.tagset.tags[0].deprecated is False
    assert result.tagset.aliases == ()
    assert result.warnings == ()


def test_spec_worked_example_maps_an_old_tag_onto_a_new_one():
    result = parse_custom_csv("tag,category,post_count,alias\nexample_tag,general,0,\nmy_old_tag,general,0,example_tag\n")
    assert [entry.name for entry in result.tagset.tags] == ["example_tag"]
    assert result.tagset.tags[0].category == 0
    assert result.tagset.aliases == ("my_old_tag",)
    assert result.tagset.tags[result.tagset.alias_target[0]].name == "example_tag"
    assert result.warnings == ()


def test_alias_target_can_be_a_main_tag_resolved_by_lookup():
    lookup = lambda name: TagEntry("blue_hair", 0, 999, False) if name == "blue_hair" else None
    result = parse_custom_csv("blu_hair,0,0,blue_hair\n", lookup=lookup)
    assert [entry.name for entry in result.tagset.tags] == ["blue_hair"]
    assert result.tagset.tags[0].post_count == 999
    assert result.tagset.aliases == ("blu_hair",)
    assert result.warnings == ()


def test_unknown_alias_target_is_reported_and_dropped():
    result = parse_custom_csv("my_tag,0,1,nope\n")
    assert result.tagset.tags == ()
    assert result.tagset.aliases == ()
    assert any("unknown alias target" in warning for warning in result.warnings)


def test_alias_pointing_at_itself_is_dropped():
    result = parse_custom_csv("my_tag,0,1,my_tag\n")
    assert result.tagset.aliases == ()
    assert any("itself" in warning for warning in result.warnings)


def test_tag_declaration_wins_over_an_alias_mapping_for_the_same_name():
    result = parse_custom_csv("a,0,1,\nb,0,1,\na,0,0,b\n")
    assert [entry.name for entry in result.tagset.tags] == ["a", "b"]
    assert result.tagset.aliases == ()
    assert any("declared as a tag" in warning for warning in result.warnings)


def test_extra_alias_targets_use_the_first_and_warn():
    result = parse_custom_csv('example_tag,0,0,\nmy_old_tag,0,0,"example_tag,other"\n')
    assert result.tagset.aliases == ("my_old_tag",)
    assert result.tagset.tags[result.tagset.alias_target[0]].name == "example_tag"
    assert any("first alias target" in warning for warning in result.warnings)


def test_header_row_is_skipped():
    result = parse_custom_csv("tag,category,post_count,alias\nmy_tag,0,1,\n")
    assert [entry.name for entry in result.tagset.tags] == ["my_tag"]


def test_names_are_normalized():
    assert parse_custom_csv("My Tag,0,1,\n").tagset.tags[0].name == "my_tag"


def test_csv_trailing_comma_is_tolerated():
    assert parse_custom_csv("a,0,1,\n").tagset.tags[0].name == "a"


def test_csv_with_too_many_columns_raises():
    with pytest.raises(ValueError, match="expected 4 columns"):
        parse_custom_csv("a,0,1,x,y\n")


def test_category_names_are_accepted():
    assert parse_custom_csv("a,character,1,\n").tagset.tags[0].category == 4
    assert parse_custom_csv("a,ARTIST,1,\n").tagset.tags[0].category == 1


def test_unparseable_category_raises():
    with pytest.raises(ValueError, match="custom CSV line 1"):
        parse_custom_csv("a,banana,1,\n")


def test_out_of_range_category_warns_and_falls_back_to_general():
    result = parse_custom_csv("a,99,1,\n")
    assert result.tagset.tags[0].category == 0
    assert any("invalid category" in warning for warning in result.warnings)


def test_empty_tag_name_is_skipped_with_a_warning():
    result = parse_custom_csv(" ,0,1,\n")
    assert result.tagset.tags == ()
    assert any("empty tag name" in warning for warning in result.warnings)


def test_json_declares_a_tag_and_an_alias():
    result = parse_custom_json('[{"tag": "example_tag"}, {"tag": "my_old_tag", "alias": ["example_tag"]}]')
    assert [entry.name for entry in result.tagset.tags] == ["example_tag"]
    assert result.tagset.aliases == ("my_old_tag",)
    assert result.warnings == ()


def test_json_accepts_a_category_name():
    assert parse_custom_json('[{"tag": "a", "category": "copyright"}]').tagset.tags[0].category == 3


def test_json_alias_as_string_is_split():
    result = parse_custom_json('[{"tag": "example_tag"}, {"tag": "old", "alias": "example_tag,other"}]')
    assert result.tagset.tags[0].name == "example_tag"
    assert result.tagset.aliases == ("old",)
    assert any("first alias target" in warning for warning in result.warnings)


def test_json_without_tag_key_raises():
    with pytest.raises(ValueError, match="'tag' key"):
        parse_custom_json('[{"name": "a"}]')


def test_load_custom_uses_the_file_extension():
    assert load_custom("a,0,1,\n", "custom_tags.csv").tagset.tags[0].name == "a"
    assert load_custom('[{"tag": "a"}]', "custom_tags.json").tagset.tags[0].name == "a"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_custom.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_custom_csv'`

- [ ] **Step 3: Append the custom parsing implementation to `artifact.py`**

Add `import csv`, `import io`, `import json` and `from typing import Callable` to the module imports at the top of `artifact.py`, then append:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_custom.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: Commit**

```bash
git add artifact.py tests/test_custom.py
git commit -m "Add custom tag parsing"
```

---

### Task 4: Source adapter base and the hlibr adapter

**Files:**
- Create: `build/__init__.py` (empty)
- Create: `build/sources/__init__.py` (empty)
- Create: `build/sources/base.py`
- Create: `build/sources/hlibr.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `artifact.normalize_tag`
- Produces:
  - `base.TagRecord(name, category, post_count, is_deprecated, aliases)`
  - `base.FetchResult(source_id, revision, data_date, files: dict[str, Path])`
  - `base.SourceData(source_id, revision, data_date, tags: dict[str, TagRecord], aliases: dict[str, str])`
  - `base.SourceAdapter` protocol: `id: str`, `fetch(cache_dir: Path) -> FetchResult`, `read(fetched: FetchResult) -> SourceData`
  - `base.sha256_of(path: Path) -> str`
  - `base.hf_dataset_revision(repo_id: str) -> str`
  - `base.hf_dataset_tree(repo_id: str, revision: str) -> list[dict]`
  - `base.hf_resolve_url(repo_id: str, revision: str, path: str) -> str`
  - `base.download(url: str, destination: Path, expected_sha256: str | None = None) -> Path`
  - `hlibr.HlibrSource` with `id = "hlibr/danbooru-tag-metadata-snapshot"` and `data_date(metadata_path: Path) -> str`

- [ ] **Step 1: Write the failing hlibr adapter test**

`tests/test_sources.py`:

```python
import json
from pathlib import Path

import pytest

from build.sources.base import FetchResult
from build.sources.hlibr import HlibrSource


def write_hlibr_fixture(directory: Path) -> FetchResult:
    pyarrow = pytest.importorskip("pyarrow")
    import pyarrow.parquet as parquet

    parquet.write_table(
        pyarrow.table({
            "name": ["Blue_Hair", "old_tag", "1girl"],
            "category": [0, 0, 0],
            "post_count": [1200, 7, 8000000],
            "is_deprecated": [False, True, False],
        }),
        directory / "tags.parquet",
    )
    parquet.write_table(
        pyarrow.table({
            "antecedent_name": ["blu_hair", "deleted_alias"],
            "consequent_name": ["blue_hair", "1girl"],
            "status": ["active", "deleted"],
        }),
        directory / "tag_aliases.parquet",
    )
    (directory / "metadata.json").write_text(
        json.dumps({"snapshot_built_at": "2026-04-08T09:15:30Z"}), encoding="utf-8"
    )
    return FetchResult(
        HlibrSource.id,
        "revision-sha",
        HlibrSource.data_date(directory / "metadata.json"),
        {
            "tags.parquet": directory / "tags.parquet",
            "tag_aliases.parquet": directory / "tag_aliases.parquet",
            "metadata.json": directory / "metadata.json",
        },
    )


def test_hlibr_normalizes_names_and_reads_deprecated(tmp_path):
    data = HlibrSource().read(write_hlibr_fixture(tmp_path))
    assert set(data.tags) == {"blue_hair", "old_tag", "1girl"}
    assert data.tags["old_tag"].is_deprecated is True
    assert data.tags["1girl"].post_count == 8000000
    assert data.data_date == "2026-04-08"


def test_hlibr_keeps_only_active_aliases(tmp_path):
    data = HlibrSource().read(write_hlibr_fixture(tmp_path))
    assert data.aliases == {"blu_hair": "blue_hair"}


def test_hlibr_missing_tag_column_raises(tmp_path):
    pyarrow = pytest.importorskip("pyarrow")
    import pyarrow.parquet as parquet

    parquet.write_table(pyarrow.table({"name": ["a"], "category": [0]}), tmp_path / "tags.parquet")
    parquet.write_table(
        pyarrow.table({"antecedent_name": ["b"], "consequent_name": ["a"], "status": ["active"]}),
        tmp_path / "tag_aliases.parquet",
    )
    fetched = FetchResult(
        HlibrSource.id,
        "rev",
        "2026-04-08",
        {"tags.parquet": tmp_path / "tags.parquet", "tag_aliases.parquet": tmp_path / "tag_aliases.parquet"},
    )
    with pytest.raises(ValueError, match="missing columns"):
        HlibrSource().read(fetched)
```

- [ ] **Step 2: Make `build` importable from tests**

`tests/conftest.py` currently ends with:

```python
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
```

Replace it with:

```python
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "build") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "build"))
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'build.sources'`

- [ ] **Step 4: Create the package files and `build/sources/base.py`**

Create empty `build/__init__.py` and `build/sources/__init__.py`.

`build/sources/base.py`:

```python
"""Shared types and HTTP helpers for upstream tag sources."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests


@dataclass(frozen=True, slots=True)
class TagRecord:
    name: str
    category: int
    post_count: int
    is_deprecated: bool
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class FetchResult:
    source_id: str
    revision: str
    data_date: str
    files: dict[str, Path]


@dataclass(frozen=True)
class SourceData:
    source_id: str
    revision: str
    data_date: str
    tags: dict[str, TagRecord]
    aliases: dict[str, str]


class SourceAdapter(Protocol):
    id: str

    def fetch(self, cache_dir: Path) -> FetchResult: ...

    def read(self, fetched: FetchResult) -> SourceData: ...


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hf_dataset_revision(repo_id: str) -> str:
    response = requests.get(f"https://huggingface.co/api/datasets/{repo_id}", timeout=30)
    response.raise_for_status()
    return response.json()["sha"]


def hf_dataset_tree(repo_id: str, revision: str) -> list[dict]:
    response = requests.get(
        f"https://huggingface.co/api/datasets/{repo_id}/tree/{revision}",
        params={"recursive": "true"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def hf_resolve_url(repo_id: str, revision: str, path: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{path}"


def download(url: str, destination: Path, expected_sha256: str | None = None) -> Path:
    """Download to `destination`, skipping work when the cached file already matches."""
    if destination.exists():
        if expected_sha256 is None or sha256_of(destination) == expected_sha256:
            return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
    os.replace(temporary, destination)
    return destination
```

- [ ] **Step 5: Write `build/sources/hlibr.py`**

```python
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
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sources.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add build/__init__.py build/sources/__init__.py build/sources/base.py build/sources/hlibr.py tests/conftest.py tests/test_sources.py
git commit -m "Add source adapter base and hlibr source"
```

---

### Task 5: HDiffusion adapter

**Files:**
- Create: `build/sources/hdiffusion.py`
- Modify: `tests/test_sources.py` (append)
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `base.FetchResult`, `SourceData`, `TagRecord`, `hf_dataset_revision`, `hf_dataset_tree`, `hf_resolve_url`, `download`
- Produces: `hdiffusion.HDiffusionSource` with `id = "HDiffusion/historical-danbooru-tag-counts"`, attribute `filename_pattern`, method `latest_entry(entries: list[dict]) -> tuple[str, str]`

- [ ] **Step 1: Write the failing HDiffusion test**

Append to `tests/test_sources.py`:

```python
from build.sources.hdiffusion import HDiffusionSource


def write_hdiffusion_fixture(directory: Path, text: str) -> FetchResult:
    path = directory / "danbooru-2026-09-22.csv"
    path.write_text(text, encoding="utf-8")
    return FetchResult(HDiffusionSource.id, "revision-sha", "2026-09-22", {"csv": path})


def test_hdiffusion_reads_headerless_csv(tmp_path):
    fetched = write_hdiffusion_fixture(
        tmp_path,
        '1girl,0,8446417,"sole_female,1girls"\nhighres,5,8198922,"high_res,high_resolution"\n',
    )
    data = HDiffusionSource().read(fetched)
    assert set(data.tags) == {"1girl", "highres"}
    assert data.tags["1girl"].post_count == 8446417
    assert data.tags["highres"].category == 5
    assert data.tags["1girl"].aliases == ("sole_female", "1girls")
    assert data.aliases == {"sole_female": "1girl", "1girls": "1girl", "high_res": "highres", "high_resolution": "highres"}


def test_hdiffusion_latest_entry_picks_newest_date():
    entries = [
        {"path": "danbooru-2026-09-20.csv"},
        {"path": "danbooru-2026-09-22.csv"},
        {"path": "README.md"},
        {"path": "danbooru-2026-09-21.csv"},
    ]
    assert HDiffusionSource().latest_entry(entries) == ("2026-09-22", "danbooru-2026-09-22.csv")


def test_hdiffusion_latest_entry_raises_without_matches():
    with pytest.raises(RuntimeError, match="no danbooru-"):
        HDiffusionSource().latest_entry([{"path": "README.md"}])


def test_hdiffusion_rejects_row_with_too_few_columns(tmp_path):
    with pytest.raises(ValueError, match="expected 3 or 4 columns"):
        HDiffusionSource().read(write_hdiffusion_fixture(tmp_path, "broken,0\n"))


def test_hdiffusion_rejects_an_extra_column(tmp_path):
    with pytest.raises(ValueError, match="expected 3 or 4 columns"):
        HDiffusionSource().read(
            write_hdiffusion_fixture(tmp_path, "1girl,0,8446417,sole_female,1girls\n")
        )


def test_hdiffusion_rejects_numeric_columns_that_are_not_numbers(tmp_path):
    with pytest.raises(ValueError):
        HDiffusionSource().read(write_hdiffusion_fixture(tmp_path, "tag,category,count\n"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sources.py -v -k hdiffusion`
Expected: FAIL with `ModuleNotFoundError: No module named 'build.sources.hdiffusion'`

- [ ] **Step 3: Write `build/sources/hdiffusion.py`**

```python
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
                if len(row) not in (3, 4):
                    raise ValueError(f"{self.id}:{line_number}: expected 3 or 4 columns, got {len(row)}")
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sources.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add build/sources/hdiffusion.py tests/test_sources.py
git commit -m "Add HDiffusion source adapter"
```

---

### Task 6: Build pipeline

**Files:**
- Create: `build/fetch_upstream.py`
- Create: `build/build_database.py`
- Create: `profiles/danbooru.yaml`
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: `artifact.TagEntry`, `TagSet`, `encode`, `normalize_tag`; `sources.base.SourceData`, `TagRecord`, `FetchResult`; `sources.hlibr.HlibrSource`; `sources.hdiffusion.HDiffusionSource`
- Produces:
  - `fetch_upstream.SOURCES: dict[str, type]`, `fetch_upstream.SOURCE_ORDER: tuple[str, ...]`
  - `fetch_upstream.fetch_all(names: list[str], cache_dir: Path) -> list[FetchResult]`
  - `build_database.Profile` dataclass with `name, threshold, exclude_categories, exclude_deprecated, extra_sources`
  - `build_database.load_profile(path: Path) -> Profile`
  - `build_database.merge_sources(source_data: list[SourceData]) -> tuple[dict[str, TagRecord], dict[str, str]]`
  - `build_database.resolve_aliases(aliases: dict[str, str], tags: dict[str, TagRecord], max_depth: int = 8) -> tuple[dict[str, str], dict[str, int]]`
  - `build_database.assemble(source_data: list[SourceData], profile: Profile) -> tuple[TagSet, dict]`
  - `build_database.write_artifacts(tagset: TagSet, out_dir: Path, *, data_version: str, profile_name: str, sources_meta: list[dict], built_at: str | None = None) -> dict`

- [ ] **Step 1: Write `profiles/danbooru.yaml`**

```yaml
name: danbooru
threshold: 25
exclude_categories: []
exclude_deprecated: true
extra_sources: []
```

- [ ] **Step 2: Write `build/fetch_upstream.py`**

```python
"""Fetch upstream tag datasets into a local raw cache.

Standalone CLI for prefetching and debugging. build_database.py calls fetch_all()
directly, so this script is not required for a build.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources.base import FetchResult  # noqa: E402
from sources.hdiffusion import HDiffusionSource  # noqa: E402
from sources.hlibr import HlibrSource  # noqa: E402

SOURCES = {"hlibr": HlibrSource, "hdiffusion": HDiffusionSource}
SOURCE_ORDER = ("hlibr", "hdiffusion")


def fetch_all(names: list[str], cache_dir: Path) -> list[FetchResult]:
    results = []
    for name in names:
        if name not in SOURCES:
            raise ValueError(f"unknown source {name!r}; available: {sorted(SOURCES)}")
        results.append(SOURCES[name]().fetch(cache_dir))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch upstream Danbooru tag datasets")
    parser.add_argument("--source", action="append", dest="sources", help="source name; repeatable")
    parser.add_argument("--cache", default="data/raw", help="raw download cache directory")
    parser.add_argument("--manifest", default="generated/raw/manifest.json")
    args = parser.parse_args(argv)

    names = args.sources or list(SOURCE_ORDER)
    results = fetch_all(names, Path(args.cache))
    manifest = {
        result.source_id: {
            "revision": result.revision,
            "data_date": result.data_date,
            "files": {key: str(value) for key, value in result.files.items()},
        }
        for result in results
    }
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for source_id, info in manifest.items():
        print(f"{source_id}: revision={info['revision'][:12]} data_date={info['data_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Write the failing build pipeline tests**

`tests/test_build.py`:

```python
import hashlib
import json

import pytest

from artifact import decode, encode, normalize_tag
from build.build_database import Profile, assemble, load_profile, merge_sources, resolve_aliases, write_artifacts
from build.sources.base import SourceData, TagRecord


def record(name, category=0, post_count=100, deprecated=False, aliases=()):
    return TagRecord(normalize_tag(name), category, post_count, deprecated, aliases)


def hlibr_data():
    return SourceData(
        "hlibr/danbooru-tag-metadata-snapshot", "rev-a", "2026-04-08",
        {
            "1girl": record("1girl", 0, 8000000),
            "blue_hair": record("blue_hair", 0, 1100),
            "rare_tag": record("rare_tag", 0, 3),
            "old_tag": record("old_tag", 0, 500, deprecated=True),
            "hatsune_miku": record("hatsune_miku", 4, 900),
        },
        {"blu_hair": "blue_hair", "oldname": "old_tag", "tobebroken": "gone_tag"},
    )


def hdiffusion_data():
    return SourceData(
        "HDiffusion/historical-danbooru-tag-counts", "rev-b", "2026-09-22",
        {
            "blue_hair": record("blue_hair", 0, 1234, aliases=("blu_hair",)),
            "brand_new": record("brand_new", 0, 200),
        },
        {"blu_hair": "blue_hair"},
    )


DEFAULT_PROFILE = Profile("danbooru", 25, frozenset(), True, ())


def test_load_profile_reads_yaml(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(
        "name: danbooru\nthreshold: 10\nexclude_categories: [5]\nexclude_deprecated: false\nextra_sources: []\n",
        encoding="utf-8",
    )
    profile = load_profile(path)
    assert profile.threshold == 10
    assert profile.exclude_categories == frozenset({5})
    assert profile.exclude_deprecated is False


def test_load_profile_defaults_when_keys_are_missing(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("name: danbooru\n", encoding="utf-8")
    profile = load_profile(path)
    assert profile.threshold == 25
    assert profile.exclude_deprecated is True
    assert profile.extra_sources == ()


def test_merge_prefers_newer_source_values_and_unions_aliases():
    tags, aliases = merge_sources([hlibr_data(), hdiffusion_data()])
    assert tags["blue_hair"].post_count == 1234
    assert tags["brand_new"].post_count == 200
    assert set(aliases) >= {"blu_hair", "oldname"}


def test_merge_keeps_deprecated_from_either_source():
    tags, _ = merge_sources([hlibr_data(), hdiffusion_data()])
    assert tags["old_tag"].is_deprecated is True


def test_resolve_aliases_follows_chains():
    tags = {"a": record("a"), "b": record("b"), "c": record("c")}
    resolved, dropped = resolve_aliases({"a": "b", "b": "c"}, tags)
    assert resolved == {"a": "c", "b": "c"}
    assert dropped["cycle"] == 0


def test_resolve_aliases_drops_cycles():
    tags = {"a": record("a"), "b": record("b")}
    resolved, dropped = resolve_aliases({"a": "b", "b": "a"}, tags)
    assert resolved == {}
    assert dropped["cycle"] == 2


def test_resolve_aliases_drops_missing_targets():
    resolved, dropped = resolve_aliases({"a": "gone"}, {"b": record("b")})
    assert resolved == {}
    assert dropped["missing"] == 1


def test_resolve_aliases_follows_chains_through_non_tag_names():
    tags = {"c": record("c")}
    resolved, dropped = resolve_aliases({"a": "b", "b": "c"}, tags)
    assert resolved == {"a": "c", "b": "c"}
    assert dropped["missing"] == 0


def test_resolve_aliases_detects_cycles_that_never_reach_a_tag():
    resolved, dropped = resolve_aliases({"a": "b", "b": "a"}, {})
    assert resolved == {}
    assert dropped["cycle"] == 2


def test_resolve_aliases_drops_a_self_mapping():
    resolved, dropped = resolve_aliases({"a": "a"}, {"a": record("a")})
    assert resolved == {}
    assert dropped["self"] == 1


def test_resolve_aliases_drops_deprecated_targets():
    tags = {"a": record("a"), "old": record("old", deprecated=True)}
    resolved, dropped = resolve_aliases({"a": "old"}, tags)
    assert resolved == {}
    assert dropped["deprecated"] == 1


def test_assemble_applies_threshold_and_tracks_alias_only_targets():
    tagset, stats = assemble([hlibr_data(), hdiffusion_data()], DEFAULT_PROFILE)
    names = [entry.name for entry in tagset.tags]
    assert "rare_tag" not in names
    assert "brand_new" in names
    assert stats["alias_only"] == 0


def test_assemble_includes_alias_targets_below_threshold():
    source = SourceData(
        "hlibr/x", "rev", "2026-04-08",
        {"popular": record("popular", 0, 5000), "tiny": record("tiny", 0, 1)},
        {"old_tiny": "tiny"},
    )
    tagset, stats = assemble([source], DEFAULT_PROFILE)
    assert "tiny" in [entry.name for entry in tagset.tags]
    assert stats["alias_only"] == 1


def test_assemble_excludes_deprecated_tags_but_keeps_their_aliases_out():
    tagset, _ = assemble([hlibr_data()], DEFAULT_PROFILE)
    names = [entry.name for entry in tagset.tags]
    assert "old_tag" not in names
    assert "oldname" not in tagset.aliases


def test_assemble_can_include_deprecated():
    profile = Profile("danbooru", 25, frozenset(), False, ())
    tagset, _ = assemble([hlibr_data()], profile)
    assert "old_tag" in [entry.name for entry in tagset.tags]


def test_assemble_applies_category_exclusion():
    profile = Profile("danbooru", 25, frozenset({4}), True, ())
    tagset, _ = assemble([hlibr_data(), hdiffusion_data()], profile)
    assert "hatsune_miku" not in [entry.name for entry in tagset.tags]


def test_assemble_output_is_encodable_and_sorted():
    tagset, _ = assemble([hlibr_data(), hdiffusion_data()], DEFAULT_PROFILE)
    names = [entry.name.encode("utf-8") for entry in tagset.tags]
    assert names == sorted(names)
    assert decode(encode(tagset)).n_tags == len(tagset.tags)


def test_write_artifacts_is_deterministic(tmp_path):
    tagset, _ = assemble([hlibr_data(), hdiffusion_data()], DEFAULT_PROFILE)
    sources = [{"id": "hlibr/x", "revision": "rev-a", "data_date": "2026-04-08"}]
    first = write_artifacts(tagset, tmp_path / "a", data_version="2026.09.22", profile_name="danbooru", sources_meta=sources, built_at="2026-09-22T00:00:00Z")
    second = write_artifacts(tagset, tmp_path / "b", data_version="2026.09.22", profile_name="danbooru", sources_meta=sources, built_at="2026-09-22T00:00:00Z")
    assert first["artifact"]["sha256"] == second["artifact"]["sha256"]
    assert first["counts"]["tags"] == len(tagset.tags)


def test_write_artifacts_metadata_sha_matches_file(tmp_path):
    tagset, _ = assemble([hlibr_data(), hdiffusion_data()], DEFAULT_PROFILE)
    metadata = write_artifacts(tagset, tmp_path, data_version="2026.09.22", profile_name="danbooru", sources_meta=[], built_at="2026-09-22T00:00:00Z")
    digest = hashlib.sha256((tmp_path / "tags.bin.gz").read_bytes()).hexdigest()
    assert metadata["artifact"]["sha256"] == digest
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8")) == metadata
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'build.build_database'`

- [ ] **Step 5: Write `build/build_database.py`**

```python
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
```

`import json` must be added to the imports at the top of `build_database.py`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build.py -v`
Expected: PASS (19 passed)

- [ ] **Step 7: Commit**

```bash
git add build/fetch_upstream.py build/build_database.py profiles/danbooru.yaml tests/test_build.py
git commit -m "Add build pipeline"
```

---

### Task 7: Artifact validation

**Files:**
- Create: `build/validate_database.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `artifact.decode`, `artifact.VALID_CATEGORIES`, `artifact.load_custom`
- Produces:
  - `validate_database.read_artifact(path: Path) -> bytes`
  - `validate_database.validate(artifact_path: Path, metadata_path: Path | None = None, post_count_limit: int = 50_000_000) -> list[str]`
  - `validate_database.validate_custom(path: Path) -> list[str]`
  - `validate_database.main(argv: list[str] | None = None) -> int`

**Contract:** `validate` returns a list of error strings and **never raises** for a bad artifact or a bad metadata file. `validate_custom` returns warning strings and **never raises** for a bad custom file. Only built-artifact problems fail the run; custom-file problems are warnings.

Structural checks (magic, format version, section alignment and bounds, offset monotonicity, name/alias sort order, `alias_target` range) belong to `artifact.decode` and are **not** re-implemented here; a decode failure becomes one error string.

- [ ] **Step 1: Write the failing validation tests**

`tests/test_validate.py`:

```python
import gzip
import hashlib
import json

from artifact import TagEntry, TagSet, VALID_CATEGORIES, encode
from build.validate_database import read_artifact, validate, validate_custom


def write_artifact(directory, tags, aliases=(), alias_target=()):
    blob = encode(TagSet(threshold=25, tags=tuple(tags), aliases=tuple(aliases), alias_target=tuple(alias_target)))
    path = directory / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(blob)
    return path


def write_metadata(directory, **overrides):
    payload = {
        "format_version": 1,
        "data_version": "2026.09.22",
        "profile": "danbooru",
        "threshold": 25,
        "counts": {"tags": 1, "aliases": 0, "deprecated": 0},
        "sources": [],
        "artifact": {"file": "tags.bin.gz", "sha256": "", "size": 0, "raw_size": 0},
    }
    payload.update(overrides)
    path = directory / "metadata.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_artifact_passes(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    assert validate(path) == []


def test_gzip_and_plain_buffers_are_both_accepted(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    plain = tmp_path / "tags.bin"
    plain.write_bytes(gzip.decompress(path.read_bytes()))
    assert read_artifact(path) == read_artifact(plain)


def test_invalid_category_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 2, 10, False)])
    assert any("invalid category" in error for error in validate(path))


def test_empty_name_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry(" ", 0, 10, False)])
    assert any("empty name" in error for error in validate(path))


def test_implausible_post_count_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 60_000_000, False)])
    assert any("implausible post_count" in error for error in validate(path))


def test_alias_name_matching_a_tag_name_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 1, False)], aliases=("a",), alias_target=(0,))
    assert any("also a tag name" in error for error in validate(path))


def test_alias_pointing_at_deprecated_target_is_reported(tmp_path):
    tags = [TagEntry("a", 0, 1, False), TagEntry("old", 0, 1, True)]
    path = write_artifact(tmp_path, tags, aliases=("legacy",), alias_target=(1,))
    assert any("target is deprecated" in error for error in validate(path))


def test_truncated_gzip_is_reported_not_raised(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    path.write_bytes(path.read_bytes()[:-8])
    errors = validate(path)
    assert errors and "could not be read" in errors[0]


def test_corrupt_gzip_body_is_reported_not_raised(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    assert validate(path)


def test_malformed_metadata_is_reported_not_raised(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{not json", encoding="utf-8")
    assert any("metadata could not be read" in error for error in validate(path, metadata))


def test_metadata_that_is_not_an_object_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    metadata = tmp_path / "metadata.json"
    metadata.write_text("[1, 2, 3]", encoding="utf-8")
    assert any("not a JSON object" in error for error in validate(path, metadata))


def test_matching_metadata_passes(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    write_metadata(
        tmp_path,
        counts={"tags": 1, "aliases": 0, "deprecated": 0},
        artifact={"file": "tags.bin.gz", "sha256": digest, "size": path.stat().st_size, "raw_size": 0},
    )
    assert validate(path, tmp_path / "metadata.json") == []


def test_metadata_count_mismatch_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    write_metadata(tmp_path, counts={"tags": 99, "aliases": 0, "deprecated": 0})
    assert any("counts.tags" in error for error in validate(path, tmp_path / "metadata.json"))


def test_metadata_deprecated_count_mismatch_is_reported(tmp_path):
    tags = [TagEntry("a", 0, 10, False), TagEntry("b", 0, 10, True)]
    path = write_artifact(tmp_path, tags)
    write_metadata(tmp_path, counts={"tags": 2, "aliases": 0, "deprecated": 0})
    assert any("counts.deprecated" in error for error in validate(path, tmp_path / "metadata.json"))


def test_metadata_threshold_mismatch_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    write_metadata(tmp_path, threshold=99)
    assert any("threshold" in error for error in validate(path, tmp_path / "metadata.json"))


def test_metadata_sha_mismatch_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    write_metadata(
        tmp_path,
        artifact={"file": "tags.bin.gz", "sha256": "deadbeef", "size": 0, "raw_size": 0},
    )
    assert any("sha256" in error for error in validate(path, tmp_path / "metadata.json"))


def test_corrupt_artifact_reports_decode_failure(tmp_path):
    path = tmp_path / "tags.bin.gz"
    path.write_bytes(b"not an artifact")
    errors = validate(path)
    assert errors and "decode failed" in errors[0]


def test_validate_custom_reports_unknown_alias_target(tmp_path):
    path = tmp_path / "custom_tags.csv"
    path.write_text("a,0,1,missing\n", encoding="utf-8")
    assert any("unknown alias target" in warning for warning in validate_custom(path))


def test_validate_custom_reports_parse_errors(tmp_path):
    path = tmp_path / "custom_tags.csv"
    path.write_text("a,0,1,x,y\n", encoding="utf-8")
    assert any("expected 4 columns" in warning for warning in validate_custom(path))


def test_validate_custom_reports_an_undecodable_file(tmp_path):
    path = tmp_path / "custom_tags.csv"
    path.write_bytes(b"\xff\xfe\x00a,0,1,\n")
    assert validate_custom(path)


def test_validate_custom_reports_a_non_object_json_entry(tmp_path):
    path = tmp_path / "custom_tags.json"
    path.write_text('[{"tag": "a", "alias": 5}]', encoding="utf-8")
    assert validate_custom(path)


def test_valid_categories_matches_danbooru():
    assert VALID_CATEGORIES == frozenset({0, 1, 3, 4, 5})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'build.validate_database'`

- [ ] **Step 3: Write `build/validate_database.py`**

```python
"""Validate a built artifact before it is released."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from artifact import VALID_CATEGORIES, decode, load_custom  # noqa: E402

READ_ERRORS = (OSError, EOFError, zlib.error, gzip.BadGzipFile)


def read_artifact(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def validate(
    artifact_path: Path,
    metadata_path: Path | None = None,
    post_count_limit: int = 50_000_000,
) -> list[str]:
    """Return error strings for a built artifact. Never raises for bad input."""
    errors: list[str] = []
    try:
        raw = read_artifact(artifact_path)
    except READ_ERRORS as exc:
        return [f"artifact could not be read: {exc}"]

    try:
        artifact = decode(raw)
    except ValueError as exc:
        return [f"artifact decode failed: {exc}"]

    deprecated_count = 0
    for index in range(artifact.n_tags):
        name = artifact.name(index)
        if not name.strip():
            errors.append(f"tag {index}: empty name")
        if artifact.category(index) not in VALID_CATEGORIES:
            errors.append(f"tag {index} ({name}): invalid category {artifact.category(index)}")
        if artifact.post_count(index) > post_count_limit:
            errors.append(f"tag {index} ({name}): implausible post_count {artifact.post_count(index)}")
        if artifact.deprecated(index):
            deprecated_count += 1

    tag_names = {artifact.name(index) for index in range(artifact.n_tags)}
    for index in range(artifact.n_aliases):
        alias = artifact.alias(index)
        if alias in tag_names:
            errors.append(f"alias {index} ({alias}): alias name is also a tag name")
        if artifact.deprecated(artifact.alias_target(index)):
            errors.append(f"alias {index} ({alias}): target is deprecated")

    if metadata_path is not None and metadata_path.exists():
        errors.extend(_validate_metadata(artifact_path, metadata_path, artifact, deprecated_count))
    return errors


def _validate_metadata(
    artifact_path: Path,
    metadata_path: Path,
    artifact,
    deprecated_count: int,
) -> list[str]:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"metadata could not be read: {exc}"]
    if not isinstance(metadata, dict):
        return ["metadata is not a JSON object"]

    errors: list[str] = []
    counts = metadata.get("counts")
    if not isinstance(counts, dict):
        errors.append("metadata counts is not an object")
        counts = {}
    artifact_meta = metadata.get("artifact")
    if not isinstance(artifact_meta, dict):
        errors.append("metadata artifact is not an object")
        artifact_meta = {}

    if counts.get("tags") != artifact.n_tags:
        errors.append(f"metadata counts.tags {counts.get('tags')} != {artifact.n_tags}")
    if counts.get("aliases") != artifact.n_aliases:
        errors.append(f"metadata counts.aliases {counts.get('aliases')} != {artifact.n_aliases}")
    if counts.get("deprecated") != deprecated_count:
        errors.append(f"metadata counts.deprecated {counts.get('deprecated')} != {deprecated_count}")
    if metadata.get("threshold") != artifact.threshold:
        errors.append(f"metadata threshold {metadata.get('threshold')} != {artifact.threshold}")
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if artifact_meta.get("sha256") != digest:
        errors.append("metadata artifact.sha256 does not match the file")
    return errors


def validate_custom(path: Path) -> list[str]:
    """Return warning strings for a custom tag file. Never raises for bad input."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return [f"custom file could not be read: {exc}"]
    try:
        result = load_custom(text, path.name)
    except (ValueError, TypeError, csv.Error) as exc:
        return [f"custom file parse error: {exc}"]
    return list(result.warnings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a built tag artifact")
    parser.add_argument("--artifact", default="generated/tags.bin.gz")
    parser.add_argument("--metadata", default="generated/metadata.json")
    parser.add_argument("--custom", action="append", dest="custom_files", default=[])
    args = parser.parse_args(argv)

    metadata_path = Path(args.metadata)
    errors = validate(Path(args.artifact), metadata_path if metadata_path.exists() else None)
    for custom_path in args.custom_files:
        for warning in validate_custom(Path(custom_path)):
            print(f"warning: {custom_path}: {warning}")

    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print("validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_validate.py -v`
Expected: PASS (22 passed)

- [ ] **Step 5: Run the whole suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add build/validate_database.py tests/test_validate.py
git commit -m "Add artifact validation"
```

---

### Task 8: Large database benchmark

**Files:**
- Modify: `pyproject.toml` (register the `slow` marker)
- Create: `tests/test_benchmark.py`

**Interfaces:**
- Consumes: `artifact.TagEntry`, `TagSet`, `encode`, `decode`, `TagIndex`
- Produces: nothing new; asserts decode time and query latency at 1,710,000 tags.

- [ ] **Step 1: Register the `slow` marker in `pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: exercises a full-size synthetic artifact; run with -m slow",
]
```

- [ ] **Step 2: Write the failing benchmark test**

`tests/test_benchmark.py`:

```python
"""Latency checks against a full-size synthetic artifact (1,710,000 tags).

Run with: .venv/bin/python -m pytest tests/test_benchmark.py -m slow -v
Skip in fast local runs with: .venv/bin/python -m pytest -m "not slow"
"""

import time

import pytest

from artifact import TagEntry, TagIndex, TagSet, decode, encode

SYNTHETIC_TAGS = 1_710_000
SYNTHETIC_ALIASES = 60_000
DECODE_BUDGET_SECONDS = 5.0
P95_BUDGET_SECONDS = 0.05


def synthetic_tagset() -> TagSet:
    tags = tuple(
        TagEntry(f"tag_{index:07d}", 0, SYNTHETIC_TAGS - index, False)
        for index in range(SYNTHETIC_TAGS)
    )
    aliases = tuple(f"alias_{index:07d}" for index in range(SYNTHETIC_ALIASES))
    return TagSet(threshold=0, tags=tags, aliases=aliases, alias_target=tuple(range(SYNTHETIC_ALIASES)))


@pytest.mark.slow
def test_full_size_decode_and_query_latency():
    blob = encode(synthetic_tagset())

    started = time.perf_counter()
    artifact = decode(blob)
    decode_seconds = time.perf_counter() - started

    index = TagIndex(artifact)
    queries = []
    step = SYNTHETIC_TAGS // 1000
    for i in range(1000):
        number = i * step
        queries.append(f"tag_{number:07d}"[:-3])
    queries += [f"alias_{i:07d}"[:-3] for i in range(0, SYNTHETIC_ALIASES, SYNTHETIC_ALIASES // 100)]

    timings = []
    for query in queries:
        started = time.perf_counter()
        index.search(query, limit=32)
        timings.append(time.perf_counter() - started)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]

    print(f"n_tags={artifact.n_tags} decode={decode_seconds:.3f}s queries={len(timings)} p95={p95 * 1000:.2f}ms")
    assert artifact.n_tags == SYNTHETIC_TAGS
    assert decode_seconds < DECODE_BUDGET_SECONDS
    assert p95 < P95_BUDGET_SECONDS
```

- [ ] **Step 3: Run the benchmark to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s`
Expected: PASS; the printed line reports `n_tags=1710000` and `p95` well below 50ms

- [ ] **Step 4: Run the whole fast suite**

Run: `.venv/bin/python -m pytest -m "not slow" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_benchmark.py
git commit -m "Add large database benchmark"
```

---

### Task 9: Run the pipeline against real upstream data

**Files:**
- Modify: `data/latest.json` (create)
- Modify: `README.md` (create a minimal build note; the full README lands in M3)

**Interfaces:**
- Consumes: the `fetch_upstream.py`, `build_database.py`, `validate_database.py` CLIs
- Produces: `generated/tags.bin.gz`, `generated/metadata.json`, `data/latest.json`

- [ ] **Step 1: Fetch and build from the real upstream datasets**

```bash
cd custom_nodes/ComfyUI-Danbooru-Tag-Autocomplete
.venv/bin/python build/fetch_upstream.py --cache data/raw
.venv/bin/python build/build_database.py --profile profiles/danbooru.yaml --out generated
```

Expected: `fetch_upstream.py` prints two source lines with revisions and data dates. `build_database.py` prints stats and a `data_version=YYYY.MM.DD` line. The build downloads roughly 60MB (hlibr tags.parquet) plus ~3.5MB (HDiffusion CSV) and may take a few minutes.

- [ ] **Step 2: Validate the produced artifact**

```bash
.venv/bin/python build/validate_database.py --artifact generated/tags.bin.gz --metadata generated/metadata.json
```

Expected: `validation passed`. If it fails, fix the pipeline — do not commit a failing artifact.

- [ ] **Step 3: Record the real numbers**

```bash
.venv/bin/python - <<'PY'
import gzip, json, sys
sys.path.insert(0, ".")
from artifact import decode
meta = json.load(open("generated/metadata.json"))
art = decode(gzip.decompress(open("generated/tags.bin.gz", "rb").read()))
print("data_version", meta["data_version"])
print("tags", art.n_tags, "aliases", art.n_aliases, "threshold", art.threshold)
print("gzip bytes", meta["artifact"]["size"], "raw bytes", meta["artifact"]["raw_size"])
PY
```

Expected: real tag/alias counts. Write these numbers into the plan's follow-up notes and use them to sanity-check the default threshold of 25.

- [ ] **Step 4: Generate `data/latest.json` from the built metadata**

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

metadata = json.loads(Path("generated/metadata.json").read_text(encoding="utf-8"))
slug = "chynggi/ComfyUI-Danbooru-Tag-Autocomplete"
version = metadata["data_version"]
latest = {
    "data_version": version,
    "profile": metadata["profile"],
    "sha256": metadata["artifact"]["sha256"],
    "size": metadata["artifact"]["size"],
    "url": f"https://github.com/{slug}/releases/download/data-{version}/tags.bin.gz",
}
Path("data").mkdir(exist_ok=True)
Path("data/latest.json").write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(latest, indent=2))
PY
```

Expected: `data/latest.json` exists with the real `data_version`, `sha256` and `size` from the build. The M3 workflow regenerates this file on every data release, so the committed copy only has to stay consistent with the release it points at.

- [ ] **Step 5: Write a minimal `README.md`**

```markdown
# ComfyUI Danbooru Tag Autocomplete

Danbooru tag autocomplete for ComfyUI, backed by continuously updated Hugging Face tag metadata.

Design: `docs/specs/2026-09-22-tag-autocomplete-design.md`
Plan (M1): `docs/plans/2026-09-22-m1-data-pipeline.md`

## Building the tag database

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python pytest pyarrow pyyaml requests
.venv/bin/python build/fetch_upstream.py --cache data/raw
.venv/bin/python build/build_database.py --profile profiles/danbooru.yaml --out generated
.venv/bin/python build/validate_database.py --artifact generated/tags.bin.gz --metadata generated/metadata.json
```

## Tests

```bash
.venv/bin/python -m pytest -m "not slow" -q
.venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s
```
```

- [ ] **Step 6: Commit**

```bash
git add data/latest.json README.md
git commit -m "Add real upstream build output pointer"
git push origin main
```

`generated/` and `data/raw/` are gitignored, so only `data/latest.json` and `README.md` are committed.

---

### Task 10: Custom tag file path resolution

**Files:**
- Modify: `artifact.py` (append a `build_custom_overlay` helper)
- Test: `tests/test_custom.py` (append)

**Interfaces:**
- Consumes: `Artifact`, `TagIndex`, `CustomParseResult`, `load_custom`
- Produces: `artifact.build_custom_overlay(main: Artifact, text: str, path_hint: str) -> tuple[Artifact | None, tuple[str, ...]]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_custom.py`:

```python
from artifact import Artifact, TagEntry, TagIndex, TagSet, build_custom_overlay


def main_artifact() -> Artifact:
    return Artifact.from_tagset(TagSet(
        threshold=25,
        tags=(TagEntry("1girl", 0, 5000, False), TagEntry("blue_hair", 0, 1200, False)),
        aliases=(),
        alias_target=(),
    ))


def test_overlay_resolves_aliases_that_point_at_main_tags():
    overlay, warnings = build_custom_overlay(main_artifact(), "blu,0,0,blue_hair\n", "custom_tags.csv")
    assert warnings == ()
    index = TagIndex(main_artifact(), custom=overlay)
    assert [hit.name for hit in index.search("blu")] == ["blue_hair"]


def test_overlay_reports_unknown_targets_and_returns_none():
    overlay, warnings = build_custom_overlay(main_artifact(), "my_tag,0,0,nope\n", "custom_tags.csv")
    assert overlay is None
    assert any("unknown alias target" in warning for warning in warnings)


def test_overlay_returns_none_for_empty_text():
    overlay, warnings = build_custom_overlay(main_artifact(), "", "custom_tags.csv")
    assert overlay is None
    assert warnings == ()


def test_overlay_parse_error_is_raised():
    with pytest.raises(ValueError):
        build_custom_overlay(main_artifact(), "a,0,1,x,y\n", "custom_tags.csv")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_custom.py -v -k overlay`
Expected: FAIL with `ImportError: cannot import name 'build_custom_overlay'`

- [ ] **Step 3: Append `build_custom_overlay` to `artifact.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_custom.py -v`
Expected: PASS (24 passed)

- [ ] **Step 5: Run the whole fast suite and commit**

Run: `.venv/bin/python -m pytest -m "not slow" -q`
Expected: PASS

```bash
git add artifact.py tests/test_custom.py
git commit -m "Add custom overlay builder"
```

---

## M1 completion criteria

- `.venv/bin/python -m pytest -m "not slow" -q` passes.
- `.venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s` passes and prints `n_tags=1710000` with p95 under 50ms.
- `generated/tags.bin.gz` and `generated/metadata.json` exist and `validate_database.py` prints `validation passed`.
- `data/latest.json` matches the built artifact's `data_version` and `sha256`.
- No file outside `custom_nodes/ComfyUI-Danbooru-Tag-Autocomplete` was modified.

## M2 and M3

- **M2** (runtime + frontend) and **M3** (CI + profiles + docs) plans are written after M1 lands, so they can describe the real `artifact.py` API instead of guessing at it.
- M2 consumes `artifact.TagIndex`, `artifact.build_custom_overlay`, `artifact.extract_token`, and `tests/fixtures/artifact.bin` + `queries.json`.
