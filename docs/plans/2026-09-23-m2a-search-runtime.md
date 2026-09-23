# Danbooru Tag Autocomplete — M2a Search Core and Runtime Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the node functional server-side and close the Python↔JavaScript search parity contract: bound the search selection so short prefixes stay inside budget, mirror the search in browser JavaScript, and add the cache/route/node runtime that serves the artifact.

**Architecture:** The Python search is re-shaped to select a bounded top-k without materialising every prefix match, and the browser gets a byte-identical port of it verified against the same fixture. A small runtime layer then discovers the artifact in the user cache directory, verifies and (if needed) downloads it, serves it over HTTP, and exposes a `Danbooru Tag Search` node that searches the same index in Python.

**Tech Stack:** Python 3.13 (pytest, requests), Node 22 (`node --test`, no npm dependencies), ComfyUI 0.37.0 / frontend 1.53.6, aiohttp (via ComfyUI's PromptServer).

**Spec:** `docs/specs/2026-09-22-tag-autocomplete-design.md`

## Global Constraints

- Project license: MIT.
- Node runtime dependencies: **0** additional. `requests` ships with ComfyUI at runtime; `pyarrow`, `pyyaml` and `requests` are build/CI/test-only in this project's own venv.
- `artifact.py` and everything under `build/` stay free of ComfyUI imports. `store.py`, `routes.py`, `nodes.py` and `__init__.py` are ComfyUI-facing and may import `folder_paths`, `server` and `comfy` — but nothing under `build/` may import them.
- Stored tag names and aliases are always lowercase; `normalize_tag` is the only normalizer.
- Tag names and aliases sort by **UTF-8 bytes ascending**; `encode` rejects unsorted input.
- Search ranking is exact (rank 0) → name prefix (rank 1) → alias prefix (rank 2). Never plain `post_count` descending. Rank-1 tie-break is name length ascending, then `post_count` descending, then name ascending. A custom entry replaces a main entry with the same canonical name.
- Search results must be **exact**: a bounded selection is allowed, an approximate one is not. Python and JavaScript must return identical results for identical input.
- Artifact format version 1, little-endian, header 64 bytes, every section 4-byte aligned. No format change in this milestone.
- Runtime module dependencies stay at zero additional; use `requests` for HTTP.
- Commit message style: `Add ...`, `Fix ...`, `Use ...`, `Remove ...` (short, imperative).
- Never modify files outside this repo folder except the two ComfyUI core files named as read-only references in this plan, which are never edited.

## Carried-forward requirement (from the M1 final review)

`docs/plans/2026-09-22-m1-post-review-corrections.md` section **C4** requires M2a to
(a) reduce the one-character-prefix query cost so it sits comfortably inside budget on
the shipped profile, and (b) make the latency gate represent real queries instead of
only long prefixes. Task 1 does both. The measurements that motivated it, taken on the
real 193,803-tag artifact: `a` 40.7 ms, `b` 24.3 ms, `blue` 0.96 ms,
`hatsune_mik` 0.30 ms, against a 50 ms budget.

## File Structure

| File | Responsibility |
|---|---|
| `artifact.py` (modify) | Search selection becomes bounded; `SearchHit` carries the name's byte length |
| `tests/test_search.py` (modify) | Bounded-selection behaviour and filter-before-selection |
| `tests/fixtures/gen_fixture.py` (modify) | Emit the new `nameLength` field |
| `tests/fixtures/queries.json` (regenerate) | The Python/JS parity contract |
| `tests/test_benchmark.py` (modify) | Short-prefix gates over a realistic prefix distribution |
| `web/search.js` (create) | Artifact decode, flat index, ranking — the browser port of `TagIndex` |
| `tests/test_search_js.mjs` (create) | Node parity test against `tests/fixtures/` |
| `store.py` (create) | Cache directory, status, download/verify/atomic-move, custom overlay, `TagIndex` cache |
| `tests/test_store.py` (create) | Store behaviour against a local HTTP server |
| `routes.py` (create) | `GET /danbooru-tag-autocomplete/{status,db,custom}` |
| `__init__.py` (create) | `WEB_DIRECTORY`, node mappings, route registration |
| `nodes.py` (create) | `Danbooru Tag Search` |
| `tests/test_nodes.py` (create) | Node behaviour, including a missing-database path |

M2b (browser UX: `web/caret.js`, `web/dropdown.js`, `web/dtautocomplete.js`, settings)
is a separate plan that consumes `web/search.js`, the `store` routes and the shared
fixture produced here.

---

### Task 1: Bound the search selection and make the latency gate representative

**Files:**
- Modify: `artifact.py` (search section)
- Modify: `tests/test_search.py`
- Modify: `tests/fixtures/gen_fixture.py`
- Regenerate: `tests/fixtures/queries.json`
- Modify: `tests/test_benchmark.py`
- Modify: `docs/specs/2026-09-22-tag-autocomplete-design.md` (§8.2 and §15 notes)

**Interfaces:**
- Consumes: `Artifact` (`lower_bound_name`, `lower_bound_alias`, `name_bytes`, `alias_bytes`, `name`, `alias`, `category`, `post_count`, `deprecated`, `alias_target`, `n_tags`, `n_aliases`), `normalize_tag`
- Produces:
  - `artifact.SearchHit(name: str, category: int, post_count: int, deprecated: bool, alias: str | None, rank: int, name_length: int)` — `name_length` is the canonical name's byte length, used only as the rank-1 tie-break
  - `artifact.TagIndex._name_candidates(source: Artifact, key_bytes: bytes, categories: frozenset[int] | None, exclude_deprecated: bool) -> Iterator[tuple[int, int, int, int]]`
  - `artifact.TagIndex._alias_hits(source: Artifact, key_bytes: bytes, categories, exclude_deprecated) -> dict[str, SearchHit]`
  - `artifact.TagIndex._search_source(source: Artifact, key_bytes: bytes, limit: int, categories, exclude_deprecated) -> dict[str, SearchHit]`
  - `artifact.TagIndex.search(query, limit=32, categories=None, exclude_deprecated=True) -> list[SearchHit]` — unchanged signature, exact results
  - the shared fixture's per-hit JSON gains `"nameLength": int`

**Why this shape.** `_collect` used to build a `SearchHit` for every prefix match and
`_search_source` then sorted all of them, calling `_hit_sort_key` (which re-encoded each
name to measure its byte length) twice per hit. For a one-character prefix on the
shipped profile that is tens of thousands of allocations and an `O(m log m)` sort for a
32-row answer. The replacement selects a bounded top-k directly:

- Name candidates are yielded as plain `(rank, name_length, -post_count, index)` tuples.
  The index is a valid stand-in for the name tie-break because a source's names are
  stored in byte order, so no name is decoded for candidates that lose.
- `heapq.nsmallest(limit, ...)` keeps only the survivors, and only those are decoded
  into `SearchHit`s.
- Alias candidates keep the existing per-canonical-name dict, because that side is small
  and genuinely needs de-duplication.
- The category and deprecated filters run **inside** the candidate generator, so a
  filtered-out candidate never consumes a selection slot. Filtering after selection
  would under-fill the result.
- The result stays exact: any candidate outside the selected set is outranked by at
  least `limit` selected candidates, and because the first merge stage is keyed by
  canonical name, de-duplication cannot shrink the set below what the top-k selection
  needs.

- [ ] **Step 1: Write the failing bounded-selection tests**

Append to `tests/test_search.py`:

```python
def test_a_short_prefix_fills_the_limit_from_ranked_candidates():
    tagset = TagSet(
        threshold=0,
        tags=tuple(TagEntry(f"a{index:04d}", 0, 10_000 - index, False) for index in range(200)),
        aliases=(),
        alias_target=(),
    )
    hits = TagIndex(Artifact.from_tagset(tagset)).search("a", limit=5)
    assert [hit.name for hit in hits] == ["a0000", "a0001", "a0002", "a0003", "a0004"]


def test_category_filter_selects_from_below_the_unfiltered_top_k():
    tags = tuple(
        TagEntry(f"a{index:04d}", 4 if index < 40 else 0, 10_000 - index, False)
        for index in range(80)
    )
    index = TagIndex(Artifact.from_tagset(TagSet(threshold=0, tags=tags, aliases=(), alias_target=())))
    hits = index.search("a", limit=3, categories=frozenset({0}))
    assert [hit.name for hit in hits] == ["a0040", "a0041", "a0042"]


def test_deprecated_filter_selects_from_below_the_unfiltered_top_k():
    tags = tuple(
        TagEntry(f"a{index:04d}", 0, 10_000 - index, index < 40)
        for index in range(80)
    )
    index = TagIndex(Artifact.from_tagset(TagSet(threshold=0, tags=tags, aliases=(), alias_target=())))
    hits = index.search("a", limit=3)
    assert [hit.name for hit in hits] == ["a0040", "a0041", "a0042"]


def test_name_length_is_the_byte_length_not_the_character_length():
    tags = (TagEntry("蓝发", 0, 5, False), TagEntry("髪", 0, 3, False))
    index = TagIndex(Artifact.from_tagset(TagSet(threshold=0, tags=tags, aliases=(), alias_target=())))
    hits = index.search("蓝")
    assert hits[0].name_length == 6
    assert hits[0].name == "蓝发"


def test_custom_entries_still_replace_main_entries_under_bounded_selection():
    main = Artifact.from_tagset(TagSet(
        threshold=0,
        tags=tuple(TagEntry(f"b{index:04d}", 0, 100 - index, False) for index in range(50)),
        aliases=(),
        alias_target=(),
    ))
    custom = Artifact.from_tagset(TagSet(
        threshold=0,
        tags=(TagEntry("b0000", 4, 999_999, False),),
        aliases=(),
        alias_target=(),
    ))
    hits = TagIndex(main, custom=custom).search("b", limit=1)
    assert [(hit.name, hit.category, hit.post_count) for hit in hits] == [("b0000", 4, 999_999)]
```

- [ ] **Step 2: Run the tests to verify they fail for the right reason**

Run: `.venv/bin/python -m pytest tests/test_search.py -v -k "short_prefix or category_filter_selects or deprecated_filter or name_length or custom_entries_still or custom_override"`
Expected: `test_name_length_is_the_byte_length_not_the_character_length` must fail with `AttributeError: 'SearchHit' object has no attribute 'name_length'`, and `test_custom_override_keeps_a_deeper_main_candidate_when_it_ranks_lower` must fail because the answer is `c0000, c0001`. The two filter tests are expected to **pass** already — the pre-change code filtered before inserting into its per-name map, so they guard the new implementation rather than demonstrating a defect; report if either fails, and report if the category and deprecated tests both pass without the filter-in-generator design being present.

- [ ] **Step 3: Replace the search section of `artifact.py`**

Add `import heapq` to the module imports and `Iterator` to the `typing` import. Replace the `SearchHit` dataclass, `_hit_sort_key`, and the whole of `TagIndex` with:

```python
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
```

The old `_collect` method is now unused — delete it.

Add this test to `tests/test_search.py` alongside the one in Step 1:

```python
def test_custom_override_keeps_a_deeper_main_candidate_when_it_ranks_lower():
    main = Artifact.from_tagset(TagSet(
        threshold=0,
        tags=(
            TagEntry("c0000", 0, 100, False),
            TagEntry("c0001", 0, 90, False),
            TagEntry("c0002", 0, 80, False),
        ),
        aliases=(),
        alias_target=(),
    ))
    custom = Artifact.from_tagset(TagSet(
        threshold=0,
        tags=(TagEntry("c0000", 4, 0, False),),
        aliases=(),
        alias_target=(),
    ))
    hits = TagIndex(main, custom=custom).search("c", limit=2)
    assert [hit.name for hit in hits] == ["c0001", "c0002"]
```

The second test is the exactness guard for overlays: the overlay replaces `c0000` with
an entry that ranks **below** the main entries it displaces, so a per-source `limit`
would lose the deeper main candidate and answer `c0000, c0001` instead of the true
`c0001, c0002`.

- [ ] **Step 4: Run the search tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_search.py tests/test_custom.py -v`
Expected: PASS (19 search + 24 custom)

- [ ] **Step 5: Update the fixture generator and regenerate the contract**

In `tests/fixtures/gen_fixture.py`, add `"nameLength": hit.name_length,` to the per-hit dict, right after `"rank"`. Then:

Run: `.venv/bin/python tests/fixtures/gen_fixture.py`
Expected: `wrote .../artifact.bin and .../queries.json`

- [ ] **Step 6: Tighten the fixture parity test to compare every field**

In `tests/test_search.py`, replace `test_shared_fixture_matches_python_search` with:

```python
def test_shared_fixture_matches_python_search():
    data = (FIXTURES / "artifact.bin").read_bytes()
    expected = json.loads((FIXTURES / "queries.json").read_text(encoding="utf-8"))
    index = TagIndex(artifact.decode(data))
    for query, hits in expected["queries"].items():
        actual = [
            {
                "name": hit.name,
                "category": hit.category,
                "postCount": hit.post_count,
                "deprecated": hit.deprecated,
                "alias": hit.alias,
                "rank": hit.rank,
                "nameLength": hit.name_length,
            }
            for hit in index.search(query)
        ]
        assert actual == hits, query
```

- [ ] **Step 7: Run the fixture test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_search.py -v -k fixture`
Expected: PASS

- [ ] **Step 8: Write the failing short-prefix latency tests**

Replace `tests/test_benchmark.py` with:

```python
"""Latency gates for the tag index.

Two synthetic sets are used, because they answer different questions:

* `tag_*` at 1,710,000 entries measures structural cost at upstream scale. Its
  names all share one prefix, so a short query would match every entry and is not
  meaningful.
* `{letter}{digits}` at the shipped profile's scale distributes the first
  character across the alphabet, so one- and two-character prefixes match a
  realistic fraction of the index and the gate measures the queries the frontend
  actually issues.

A third gate runs against the real artifact when `generated/tags.bin.gz` exists,
which is after a build and in the CI job that builds before testing.

Run everything with: .venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s
"""

import gzip
import string
import time
from pathlib import Path

import pytest

from artifact import Artifact, TagEntry, TagIndex, TagSet, decode, encode

SYNTHETIC_TAGS = 1_710_000
SYNTHETIC_ALIASES = 60_000
SHIPPED_TAGS = 1_709_994
DECODE_BUDGET_SECONDS = 5.0
LONG_PREFIX_P95_BUDGET_SECONDS = 0.05
SHORT_PREFIX_P95_BUDGET_SECONDS = 0.05
REAL_ARTIFACT = Path("generated/tags.bin.gz")


def tagset_with_shared_prefix(count: int) -> TagSet:
    tags = tuple(TagEntry(f"tag_{index:07d}", 0, count - index, False) for index in range(count))
    aliases = tuple(f"alias_{index:07d}" for index in range(SYNTHETIC_ALIASES))
    return TagSet(threshold=0, tags=tags, aliases=aliases, alias_target=tuple(range(SYNTHETIC_ALIASES)))


def tagset_with_distributed_prefix(count: int) -> TagSet:
    per_letter = count // len(string.ascii_lowercase)
    tags = tuple(
        TagEntry(f"{letter}{index:07d}", 0, count - (letter_index * per_letter + index), False)
        for letter_index, letter in enumerate(string.ascii_lowercase)
        for index in range(per_letter)
    )
    return TagSet(threshold=0, tags=tags, aliases=(), alias_target=())


def p95(seconds: list[float]) -> float:
    ordered = sorted(seconds)
    return ordered[int(len(ordered) * 0.95)]


def timed_queries(index: TagIndex, queries: list[str]) -> float:
    timings = []
    for query in queries:
        started = time.perf_counter()
        index.search(query, limit=32)
        timings.append(time.perf_counter() - started)
    return p95(timings)


@pytest.mark.slow
def test_full_size_decode_and_long_prefix_latency():
    blob = encode(tagset_with_shared_prefix(SYNTHETIC_TAGS))

    started = time.perf_counter()
    artifact = decode(blob)
    decode_seconds = time.perf_counter() - started

    index = TagIndex(artifact)
    step = SYNTHETIC_TAGS // 1000
    queries = [f"tag_{i * step:07d}"[:-3] for i in range(1000)]
    queries += [f"alias_{i:07d}"[:-3] for i in range(0, SYNTHETIC_ALIASES, SYNTHETIC_ALIASES // 100)]

    measured = timed_queries(index, queries)
    print(f"n_tags={artifact.n_tags} decode={decode_seconds:.3f}s queries={len(queries)} p95={measured * 1000:.2f}ms")
    assert artifact.n_tags == SYNTHETIC_TAGS
    assert decode_seconds < DECODE_BUDGET_SECONDS
    assert measured < LONG_PREFIX_P95_BUDGET_SECONDS


@pytest.mark.slow
def test_shipped_profile_short_prefix_latency():
    index = TagIndex(decode(encode(tagset_with_distributed_prefix(SHIPPED_TAGS))))

    single = timed_queries(index, list(string.ascii_lowercase))
    double = timed_queries(index, [a + b for a in "abcd" for b in string.ascii_lowercase])

    print(f"1-char p95={single * 1000:.2f}ms 2-char p95={double * 1000:.2f}ms")
    assert single < SHORT_PREFIX_P95_BUDGET_SECONDS
    assert double < SHORT_PREFIX_P95_BUDGET_SECONDS


@pytest.mark.slow
@pytest.mark.skipif(not REAL_ARTIFACT.exists(), reason="run a build before this gate")
def test_real_artifact_short_prefix_latency():
    index = TagIndex(decode(gzip.decompress(REAL_ARTIFACT.read_bytes())))

    single = timed_queries(index, list(string.ascii_lowercase))
    double = timed_queries(index, [a + b for a in "abcd" for b in string.ascii_lowercase])

    print(f"real 1-char p95={single * 1000:.2f}ms 2-char p95={double * 1000:.2f}ms")
    assert single < SHORT_PREFIX_P95_BUDGET_SECONDS
    assert double < SHORT_PREFIX_P95_BUDGET_SECONDS
```

- [ ] **Step 9: Measure and, if a budget is missed, stop and report**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s`
Expected: PASS, with lines like
`n_tags=1710000 decode=0.4s ... p95=...`,
`1-char p95=... 2-char p95=...`,
`real 1-char p95=... 2-char p95=...`.

Copy all three printed lines into your report. **If any assertion fails, do not raise the
budget.** Report the measured numbers and which budget was missed — the controller will
decide whether the selection needs a different algorithm or the budget needs a ruling.

- [ ] **Step 10: Record the bounded selection and the gates in the spec**

In `docs/specs/2026-09-22-tag-autocomplete-design.md`:

- in §8.2, append this sentence: `선택은 bounded top-k로 수행한다. 이름 후보는 필터를 통과한 뒤 (rank, 이름 byte 길이, -post_count, index) 튜플로 비교하고 heapq.nsmallest로 상위 limit만 남긴 뒤 디코드한다. 카테고리와 deprecated 필터는 선택 이전에 적용하므로 필터가 슬롯을 소모하지 않는다. 결과는 근사가 아니라 정확하다.`
- in §15, append this row to the pytest table:

```markdown
| `test_benchmark.py` | 합성 1,710,000 태그로 decode와 긴 prefix 질의 p95, 그리고 shipped 프로필 규모(1,709,994)의 1글자·2글자 prefix p95를 측정한다. `generated/tags.bin.gz`가 있으면 실제 아티팩트에 대해서도 같은 게이트를 실행한다 |
```

- [ ] **Step 11: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (104 + the new gates)

```bash
git add artifact.py tests/test_search.py tests/test_benchmark.py tests/fixtures docs/specs/2026-09-22-tag-autocomplete-design.md
git commit -m "Bound tag search selection and gate short prefixes"
```

---

### Task 2: Browser search port with Node parity

**Files:**
- Create: `web/search.js`
- Create: `web/package.json`
- Create: `tests/test_search_js.mjs`
- Modify: `tests/fixtures/gen_fixture.py`
- Modify: `tests/fixtures/queries.json` (regenerated)
- Create: `tests/fixtures/overlay-main.bin`, `tests/fixtures/overlay.bin` (generated)
- Modify: `tests/test_search.py` (add the overlay parity test)

**Interfaces:**
- Consumes: the artifact layout from `artifact.py`, and `tests/fixtures/artifact.bin` + `tests/fixtures/queries.json`
- Produces (ES module exports):
  - `RANK_EXACT = 0`, `RANK_NAME_PREFIX = 1`, `RANK_ALIAS_PREFIX = 2`
  - `normalizeTag(name: string) -> string`
  - `extractToken(text: string, caret: number) -> string`
  - `decodeArtifact(buffer: ArrayBuffer) -> ArtifactView`, where `ArtifactView` has `threshold`, `nTags`, `nAliases`, `names`, `nameOffsets`, `category`, `postCount`, `tagFlags`, `aliasNames`, `aliasOffsets`, `aliasTarget`
  - `class TagIndex { constructor(main, custom = null); search(query, { limit = 32, categories = null, excludeDeprecated = true } = {}) }` returning `[{ name, category, postCount, deprecated, alias, rank, nameLength }]`
  - `ArtifactView` helper methods `name(index) -> string`, `alias(index) -> string`
  - the shared fixture gains `tests/fixtures/overlay-main.bin`, `tests/fixtures/overlay.bin`, and an `overlay` section in `queries.json` shaped `{"limit": int, "queries": {query: [hit, ...]}}`, so the custom-overlay path is part of the parity contract

**Why a port rather than a shared runtime.** The browser cannot run Python. Parity is
enforced by the shared fixture: Task 1 regenerated `queries.json` from the Python
implementation and the Node test below asserts the JavaScript implementation produces
byte-identical results for every query in it.

The JavaScript side may select differently *internally* (it collects and sorts) because
both sides are exact; only the results are contractual.

- [ ] **Step 1: Write the failing Node parity test**

Create `tests/test_search_js.mjs`:

```javascript
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { TagIndex, decodeArtifact, extractToken, normalizeTag } from "../web/search.js";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures");
const artifactBytes = readFileSync(join(FIXTURES, "artifact.bin"));
const artifactBuffer = artifactBytes.buffer.slice(
  artifactBytes.byteOffset,
  artifactBytes.byteOffset + artifactBytes.byteLength,
);
const expected = JSON.parse(readFileSync(join(FIXTURES, "queries.json"), "utf8"));

function project(hit) {
  return {
    name: hit.name,
    category: hit.category,
    postCount: hit.postCount,
    deprecated: hit.deprecated,
    alias: hit.alias,
    rank: hit.rank,
    nameLength: hit.nameLength,
  };
}

test("decodeArtifact reads the header and sections", () => {
  const artifact = decodeArtifact(artifactBuffer);
  assert.equal(artifact.threshold, expected.threshold);
  assert.equal(artifact.name(0), "1girl");
  assert.ok(artifact.nTags > 0);
});

test("search matches the Python fixture for every query", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  for (const [query, hits] of Object.entries(expected.queries)) {
    assert.deepEqual(index.search(query).map(project), hits, `query ${JSON.stringify(query)}`);
  }
});

test("limit is respected", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  assert.ok(index.search("blue_h", { limit: 2 }).length <= 2);
});

test("deprecated matches are excluded by default and can be included", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  assert.deepEqual(index.search("old_tag"), []);
  const included = index.search("old_tag", { excludeDeprecated: false });
  assert.ok(included.length >= 1);
  assert.equal(included[0].deprecated, true);
});

test("normalizeTag folds case and spaces to underscores", () => {
  assert.equal(normalizeTag("  Blue Hair "), "blue_hair");
});

test("extractToken reads the token under the caret only", () => {
  assert.equal(extractToken("1girl, blue_h", 13), "blue_h");
  assert.equal(extractToken("blue_hair, 1girl", 4), "blue");
  assert.equal(extractToken("", 0), "");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test tests/test_search_js.mjs`
Expected: FAIL with `Cannot find module .../web/search.js`

- [ ] **Step 3: Write `web/package.json` and `web/search.js`**

`web/package.json` — Node treats a `.js` file as CommonJS unless the nearest
`package.json` says otherwise, and the browser module below uses `export`. The browser
does not care about this file (ComfyUI's `/extensions` listing globs `**/*.js` only, so
it is never served), and it declares no dependencies, so nothing installs it.

```json
{
  "private": true,
  "type": "module"
}
```

`web/search.js`:

```javascript
// Browser port of artifact.TagIndex. The Python implementation in artifact.py is the
// reference; tests/test_search_js.mjs asserts this file produces identical results for
// every query in tests/fixtures/queries.json.
//
// Artifact layout (format version 1, little-endian, header 64 bytes, sections 4-byte
// aligned): names, name_offsets, category, post_count, tag_flags, alias_names,
// alias_offsets, alias_target.

export const RANK_EXACT = 0;
export const RANK_NAME_PREFIX = 1;
export const RANK_ALIAS_PREFIX = 2;

const MAGIC = 0x31415444; // "DTA1"
const FORMAT_VERSION = 1;
const HEADER_SIZE = 64;

const decoder = new TextDecoder("utf-8", { fatal: true });

export function normalizeTag(name) {
  return name.trim().toLowerCase().replace(/ /g, "_");
}

const TOKEN_SEPARATORS = new Set([",", "\n", ";"]);

export function extractToken(text, caret) {
  const stop = Math.max(0, Math.min(caret, text.length));
  let start = 0;
  for (let index = stop - 1; index >= 0; index -= 1) {
    if (TOKEN_SEPARATORS.has(text[index])) {
      start = index + 1;
      break;
    }
  }
  return normalizeTag(text.slice(start, stop));
}

export class ArtifactView {
  constructor(buffer) {
    if (buffer.byteLength < HEADER_SIZE) {
      throw new Error("artifact is smaller than the 64-byte header");
    }
    const view = new DataView(buffer);
    if (view.getUint32(0, true) !== MAGIC) {
      throw new Error("bad magic");
    }
    if (view.getUint16(4, true) !== FORMAT_VERSION) {
      throw new Error(`unsupported format_version: ${view.getUint16(4, true)}`);
    }
    const offsets = (position) => view.getUint32(position, true);
    const offNames = offsets(20);
    const offNameOffsets = offsets(24);
    const offCategory = offsets(28);
    const offPostCount = offsets(32);
    const offTagFlags = offsets(36);
    const offAliasNames = offsets(40);
    const offAliasOffsets = offsets(44);
    const offAliasTarget = offsets(48);
    const lengthNames = offsets(52);
    const lengthAliasNames = offsets(56);

    for (const [label, offset] of [
      ["names", offNames],
      ["name_offsets", offNameOffsets],
      ["category", offCategory],
      ["post_count", offPostCount],
      ["tag_flags", offTagFlags],
      ["alias_names", offAliasNames],
      ["alias_offsets", offAliasOffsets],
      ["alias_target", offAliasTarget],
    ]) {
      if (offset % 4 !== 0) {
        throw new Error(`section ${label} is not 4-byte aligned: ${offset}`);
      }
    }

    this.threshold = offsets(16);
    this.nTags = offsets(8);
    this.nAliases = offsets(12);
    this.names = new Uint8Array(buffer, offNames, lengthNames);
    this.nameOffsets = new Uint32Array(buffer, offNameOffsets, this.nTags + 1);
    this.category = new Uint8Array(buffer, offCategory, this.nTags);
    this.postCount = new Uint32Array(buffer, offPostCount, this.nTags);
    this.tagFlags = new Uint8Array(buffer, offTagFlags, Math.ceil(this.nTags / 8));
    this.aliasNames = new Uint8Array(buffer, offAliasNames, lengthAliasNames);
    this.aliasOffsets = new Uint32Array(buffer, offAliasOffsets, this.nAliases + 1);
    this.aliasTarget = new Uint32Array(buffer, offAliasTarget, this.nAliases);
  }

  nameBytes(index) {
    return this.names.subarray(this.nameOffsets[index], this.nameOffsets[index + 1]);
  }

  name(index) {
    return decoder.decode(this.nameBytes(index));
  }

  alias(index) {
    return decoder.decode(this.aliasNames.subarray(this.aliasOffsets[index], this.aliasOffsets[index + 1]));
  }

  deprecated(index) {
    return (this.tagFlags[index >> 3] & (1 << (index & 7))) !== 0;
  }
}

export function decodeArtifact(buffer) {
  return new ArtifactView(buffer);
}

function compareSlice(bytes, start, end, key) {
  const shared = Math.min(end - start, key.length);
  for (let index = 0; index < shared; index += 1) {
    const left = bytes[start + index];
    const right = key[index];
    if (left !== right) {
      return left < right ? -1 : 1;
    }
  }
  const length = end - start;
  if (length === key.length) {
    return 0;
  }
  return length < key.length ? -1 : 1;
}

function lowerBound(bytes, offsets, count, key) {
  let low = 0;
  let high = count;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (compareSlice(bytes, offsets[mid], offsets[mid + 1], key) < 0) {
      low = mid + 1;
    } else {
      high = mid;
    }
  }
  return low;
}

function startsWith(bytes, start, end, key) {
  if (end - start < key.length) {
    return false;
  }
  for (let index = 0; index < key.length; index += 1) {
    if (bytes[start + index] !== key[index]) {
      return false;
    }
  }
  return true;
}

function compareCodePoints(left, right) {
  // Python compares str by code point, and UTF-8 byte order agrees with code-point order.
  // JavaScript compares by UTF-16 code unit, which sorts astral characters before BMP
  // characters above U+E000, so compare code points explicitly.
  let leftIndex = 0;
  let rightIndex = 0;
  while (leftIndex < left.length && rightIndex < right.length) {
    const leftPoint = left.codePointAt(leftIndex);
    const rightPoint = right.codePointAt(rightIndex);
    if (leftPoint !== rightPoint) {
      return leftPoint < rightPoint ? -1 : 1;
    }
    leftIndex += leftPoint > 0xffff ? 2 : 1;
    rightIndex += rightPoint > 0xffff ? 2 : 1;
  }
  if (leftIndex >= left.length && rightIndex >= right.length) {
    return 0;
  }
  return leftIndex >= left.length ? -1 : 1;
}

function compareHits(left, right) {
  if (left.rank !== right.rank) {
    return left.rank < right.rank ? -1 : 1;
  }
  const leftLength = left.rank === RANK_NAME_PREFIX ? left.nameLength : 0;
  const rightLength = right.rank === RANK_NAME_PREFIX ? right.nameLength : 0;
  if (leftLength !== rightLength) {
    return leftLength < rightLength ? -1 : 1;
  }
  if (left.postCount !== right.postCount) {
    return left.postCount > right.postCount ? -1 : 1;
  }
  return compareCodePoints(left.name, right.name);
}

export class TagIndex {
  constructor(main, custom = null) {
    this.main = main;
    this.custom = custom;
  }

  #nameHits(source, keyBytes, categories, excludeDeprecated) {
    const hits = [];
    for (let index = lowerBound(source.names, source.nameOffsets, source.nTags, keyBytes); index < source.nTags; index += 1) {
      const start = source.nameOffsets[index];
      const end = source.nameOffsets[index + 1];
      if (!startsWith(source.names, start, end, keyBytes)) {
        break;
      }
      if (excludeDeprecated && source.deprecated(index)) {
        continue;
      }
      const category = source.category[index];
      if (categories !== null && !categories.has(category)) {
        continue;
      }
      const rank = end - start === keyBytes.length ? RANK_EXACT : RANK_NAME_PREFIX;
      hits.push({
        name: source.name(index),
        category,
        postCount: source.postCount[index],
        deprecated: source.deprecated(index),
        alias: null,
        rank,
        nameLength: end - start,
      });
    }
    return hits;
  }

  #aliasHits(source, keyBytes, categories, excludeDeprecated) {
    const best = new Map();
    for (let index = lowerBound(source.aliasNames, source.aliasOffsets, source.nAliases, keyBytes); index < source.nAliases; index += 1) {
      const start = source.aliasOffsets[index];
      const end = source.aliasOffsets[index + 1];
      if (!startsWith(source.aliasNames, start, end, keyBytes)) {
        break;
      }
      const target = source.aliasTarget[index];
      if (excludeDeprecated && source.deprecated(target)) {
        continue;
      }
      const category = source.category[target];
      if (categories !== null && !categories.has(category)) {
        continue;
      }
      const name = source.name(target);
      const hit = {
        name,
        category,
        postCount: source.postCount[target],
        deprecated: source.deprecated(target),
        alias: source.alias(index),
        rank: RANK_ALIAS_PREFIX,
        nameLength: source.nameOffsets[target + 1] - source.nameOffsets[target],
      };
      const current = best.get(name);
      if (current === undefined || compareHits(hit, current) < 0) {
        best.set(name, hit);
      }
    }
    return best;
  }

  #searchSource(source, keyBytes, limit, categories, excludeDeprecated) {
    const combined = new Map();
    for (const hit of this.#nameHits(source, keyBytes, categories, excludeDeprecated)) {
      combined.set(hit.name, hit);
    }
    for (const [name, hit] of this.#aliasHits(source, keyBytes, categories, excludeDeprecated)) {
      const current = combined.get(name);
      if (current === undefined || compareHits(hit, current) < 0) {
        combined.set(name, hit);
      }
    }
    return [...combined.values()].sort(compareHits).slice(0, limit);
  }

  search(query, { limit = 32, categories = null, excludeDeprecated = true } = {}) {
    const key = normalizeTag(query);
    if (key === "") {
      return [];
    }
    const keyBytes = new TextEncoder().encode(key);
    const merged = new Map();
    if (this.custom === null) {
      for (const hit of this.#searchSource(this.main, keyBytes, limit, categories, excludeDeprecated)) {
        merged.set(hit.name, hit);
      }
    } else {
      // The overlay wins by name even when it ranks lower than the main entry it
      // replaces, so over-select main by the number of overlay names to keep the
      // bounded window exact. Keep this in step with TagIndex.search in artifact.py.
      const custom = this.#searchSource(
        this.custom,
        keyBytes,
        this.custom.nTags + this.custom.nAliases,
        categories,
        excludeDeprecated,
      );
      for (const hit of this.#searchSource(this.main, keyBytes, limit + custom.length, categories, excludeDeprecated)) {
        merged.set(hit.name, hit);
      }
      for (const hit of custom) {
        merged.set(hit.name, hit);
      }
    }
    return [...merged.values()].sort(compareHits).slice(0, limit);
  }
}
```

- [ ] **Step 4: Run the Node test to verify it passes**

Run: `node --test tests/test_search_js.mjs`
Expected: PASS (`# pass 6`), output free of warnings

- [ ] **Step 5: Extend the shared contract with an overlay case**

The fixture has no custom overlay, so the parity test would not catch a divergence in the
overlay path — which is the path the browser actually uses once a user adds custom tags.
Close that gap in the contract itself.

Replace `tests/fixtures/gen_fixture.py` with:

```python
"""Regenerate the shared Python/JavaScript parity fixtures.

Run from the repo root: .venv/bin/python tests/fixtures/gen_fixture.py

Writes:
  artifact.bin, overlay-main.bin, overlay.bin  - encoded artifacts
  queries.json                                 - the expected results for both indexes

The Node test in tests/test_search_js.mjs consumes the same files, so the two
implementations are checked against one expected result set.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from artifact import TagEntry, TagIndex, TagSet, decode, encode  # noqa: E402

TAGSET = TagSet(
    threshold=25,
    tags=tuple(sorted(
        (
            TagEntry("1girl", 0, 5000, False),
            TagEntry("blue_hair", 0, 1200, False),
            TagEntry("blue_hair_ornament", 0, 30, False),
            TagEntry("blue_hairband", 0, 82, False),
            TagEntry("hatsune_miku", 4, 900, False),
            TagEntry("highres", 5, 700, False),
            TagEntry("old_tag", 0, 10, True),
            # Two names that tie on rank, UTF-8 byte length and post_count, so the final
            # name tie-break decides between them. JavaScript compares UTF-16 code units
            # and would order them the other way round; see compareCodePoints in
            # web/search.js.
            TagEntry("x\ue000x", 0, 7, False),
            TagEntry("x\U00010000", 0, 7, False),
        ),
        key=lambda entry: entry.name.encode("utf-8"),
    )),
    aliases=("blu_hair", "miku", "oldtag"),
    alias_target=(1, 4, 4),
)

QUERIES = ["blue_h", "blue_hair", "blu_h", "miku", "old_tag", "high", "x", "zzz", ""]

# An overlay that replaces a main tag with an entry ranking below the one it displaces,
# which is the case a bounded per-source window gets wrong if it is not over-selected.
OVERLAY_MAIN = TagSet(
    threshold=0,
    tags=(
        TagEntry("c0", 0, 100, False),
        TagEntry("c1", 0, 90, False),
        TagEntry("c2", 0, 80, False),
    ),
    aliases=(),
    alias_target=(),
)

OVERLAY = TagSet(
    threshold=0,
    tags=(TagEntry("c0", 4, 0, False),),
    aliases=(),
    alias_target=(),
)

OVERLAY_QUERIES = ["c"]
OVERLAY_LIMIT = 1

HERE = Path(__file__).resolve().parent


def project(hit):
    return {
        "name": hit.name,
        "category": hit.category,
        "postCount": hit.post_count,
        "deprecated": hit.deprecated,
        "alias": hit.alias,
        "rank": hit.rank,
        "nameLength": hit.name_length,
    }


for filename, tagset in (("artifact.bin", TAGSET), ("overlay-main.bin", OVERLAY_MAIN), ("overlay.bin", OVERLAY)):
    (HERE / filename).write_bytes(encode(tagset))

index = TagIndex(decode((HERE / "artifact.bin").read_bytes()))
expected = {query: [project(hit) for hit in index.search(query)] for query in QUERIES}

overlay_index = TagIndex(
    decode((HERE / "overlay-main.bin").read_bytes()),
    custom=decode((HERE / "overlay.bin").read_bytes()),
)
overlay_expected = {
    query: [project(hit) for hit in overlay_index.search(query, limit=OVERLAY_LIMIT)]
    for query in OVERLAY_QUERIES
}

(HERE / "queries.json").write_text(
    json.dumps(
        {
            "threshold": TAGSET.threshold,
            "queries": expected,
            "overlay": {"limit": OVERLAY_LIMIT, "queries": overlay_expected},
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print("wrote", HERE / "artifact.bin", HERE / "overlay-main.bin", HERE / "overlay.bin", "and", HERE / "queries.json")
```

Run: `.venv/bin/python tests/fixtures/gen_fixture.py`
Expected: prints the four paths

Append to `tests/test_search.py`:

```python
def test_shared_fixture_overlay_matches_python_search():
    expected = json.loads((FIXTURES / "queries.json").read_text(encoding="utf-8"))
    overlay = expected["overlay"]
    index = TagIndex(
        artifact.decode((FIXTURES / "overlay-main.bin").read_bytes()),
        custom=artifact.decode((FIXTURES / "overlay.bin").read_bytes()),
    )
    for query, hits in overlay["queries"].items():
        actual = [
            {
                "name": hit.name,
                "category": hit.category,
                "postCount": hit.post_count,
                "deprecated": hit.deprecated,
                "alias": hit.alias,
                "rank": hit.rank,
                "nameLength": hit.name_length,
            }
            for hit in index.search(query, limit=overlay["limit"])
        ]
        assert actual == hits, query
```

Add to `tests/test_search_js.mjs`:

```javascript
test("overlay results match the Python fixture", () => {
  const overlay = expected.overlay;
  const main = decodeArtifact(readBuffer(join(FIXTURES, "overlay-main.bin")));
  const custom = decodeArtifact(readBuffer(join(FIXTURES, "overlay.bin")));
  const index = new TagIndex(main, custom);
  for (const [query, hits] of Object.entries(overlay.queries)) {
    assert.deepEqual(index.search(query, { limit: overlay.limit }).map(project), hits, `query ${JSON.stringify(query)}`);
  }
});
```

Add the `readBuffer` helper next to the artifact read at the top of `tests/test_search_js.mjs`,
and use it for the main artifact too:

```javascript
function readBuffer(path) {
  const bytes = readFileSync(path);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

const artifactBuffer = readBuffer(join(FIXTURES, "artifact.bin"));
```

Add to `tests/test_search.py`:

```python
def test_astral_names_tie_break_by_code_point_not_utf16_unit():
    index = TagIndex(artifact.decode((FIXTURES / "artifact.bin").read_bytes()))
    assert [hit.name for hit in index.search("x")] == ["x\ue000x", "x\U00010000"]
```

Add to `tests/test_search_js.mjs`:

```javascript
test("astral names tie-break by code point, not UTF-16 code unit", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  assert.deepEqual(index.search("x").map((hit) => hit.name), ["x\ue000x", "x\u{10000}"]);
});
```

Run: `.venv/bin/python -m pytest tests/test_search.py -v && node --test tests/test_search_js.mjs`
Expected: PASS (22 Python search tests, 8 Node tests)

- [ ] **Step 6: Commit**

```bash
git add web/search.js web/package.json tests/test_search_js.mjs tests/fixtures tests/test_search.py
git commit -m "Add browser tag search with Node parity"
```

---

### Task 3: Runtime store

**Files:**
- Modify: `artifact.py` (add `Artifact.to_bytes`)
- Create: `store.py`
- Modify: `tests/conftest.py` (add the `node_package` fixture)
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: `artifact.Artifact`, `TagIndex`, `build_custom_overlay`, `decode`
- Produces:
  - `store.CACHE_DIRNAME`, `store.ARTIFACT_FILENAME`, `store.METADATA_FILENAME`, `store.CUSTOM_FILENAMES`, `store.DEFAULT_REPO_SLUG`
  - `store.STATE_READY`, `store.STATE_DOWNLOADING`, `store.STATE_MISSING`, `store.STATE_ERROR`
  - `store.env vars`: `DTA_LATEST_URL`, `DTA_REPO_SLUG`, `DTA_LOCAL_ARTIFACT`
  - `store.Status(state: str, data_version: str | None, error: str | None)`
  - `store.cache_dir() -> Path`
  - `store.artifact_path() -> Path`, `store.metadata_path() -> Path`, `store.custom_file_path() -> Path | None`
  - `store.status() -> Status`
  - `store.ensure_download() -> None` — idempotent, non-blocking, never raises
  - `store.load_artifact() -> Artifact` — cached by `(path, mtime_ns, size)`
  - `store.load_custom() -> tuple[Artifact | None, tuple[str, ...]]` — cached by `(path, mtime_ns)`
  - `store.load_index() -> TagIndex`
  - `store.artifact_content_encoding() -> str | None` — `"gzip"` when the file starts with the gzip magic, else `None`
  - `store.custom_payload() -> dict` — `{"available": bool, "warnings": [str], "bytes": <base64 str | null>}`

**Why the module is importable outside ComfyUI.** `store.py`'s only ComfyUI dependency is
the user directory, so it imports `folder_paths` inside `cache_dir()` instead of at module
scope. That keeps the module importable in this project's own venv, which is what makes
the tests below possible. `store.py` may import ComfyUI modules; nothing under `build/` may.

`DTA_LOCAL_ARTIFACT` points `artifact_path()` and `metadata_path()` at a build output
directory (for example `generated/`), which is how this milestone is verified before the
release asset exists.

- [ ] **Step 1: Add `Artifact.to_bytes`**

In `artifact.py`, inside `Artifact`, directly after `to_tagset`:

```python
    def to_bytes(self) -> bytes:
        """Return the encoded artifact, byte-identical to what the encoder produced."""
        return self._buffer
```

Add a test to `tests/test_artifact.py`:

```python
def test_to_bytes_returns_the_encoded_buffer():
    data = encode(sample_tagset())
    assert decode(data).to_bytes() == data
```

Run: `.venv/bin/python -m pytest tests/test_artifact.py -v`
Expected: PASS (11 passed)

- [ ] **Step 2: Add the `node_package` fixture**

`__init__.py` registers HTTP routes and nodes, which needs a running ComfyUI, so tests
must import the individual modules without executing it. Append to `tests/conftest.py`:

```python
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
```

- [ ] **Step 3: Write the failing store tests**

Create `tests/test_store.py`:

```python
import base64
import functools
import gzip
import hashlib
import http.server
import importlib
import json
import threading
import time
from pathlib import Path

import pytest

from artifact import TagEntry, TagSet, encode


def build_artifact(directory: Path) -> Path:
    tagset = TagSet(
        threshold=25,
        tags=(
            TagEntry("1girl", 0, 5000, False),
            TagEntry("blue_hair", 0, 1200, False),
            TagEntry("hatsune_miku", 4, 900, False),
        ),
        aliases=("blu_hair",),
        alias_target=(1,),
    )
    directory.mkdir(parents=True, exist_ok=True)
    raw = encode(tagset)
    path = directory / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(raw)
    metadata = {
        "format_version": 1,
        "data_version": "2026.09.22",
        "profile": "danbooru",
        "threshold": 25,
        "counts": {"tags": 3, "aliases": 1, "deprecated": 0},
        "sources": [],
        "artifact": {
            "file": "tags.bin.gz",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
            "raw_size": len(raw),
        },
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return path


@pytest.fixture
def store(node_package, tmp_path, monkeypatch):
    module = importlib.import_module(f"{node_package}.store")
    monkeypatch.setattr(module, "_state", module.STATE_MISSING)
    monkeypatch.setattr(module, "_error", None)
    monkeypatch.setattr(module, "_download_thread", None)
    monkeypatch.setattr(module, "_artifact_cache", None)
    monkeypatch.setattr(module, "_custom_cache", None)
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(module, "cache_dir", lambda: cache)
    return module


@pytest.fixture
def http_files(tmp_path):
    root = tmp_path / "served"
    root.mkdir()

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def wait_for_state(store, expected, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.status().state == expected:
            return store.status()
        time.sleep(0.02)
    raise AssertionError(f"state never became {expected!r}; last was {store.status()!r}")


def test_local_artifact_override_is_ready_without_network(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))

    assert store.artifact_path() == source
    assert store.status().state == store.STATE_READY
    assert store.status().data_version == "2026.09.22"

    store.ensure_download()
    assert store.status().state == store.STATE_READY


def test_load_index_searches_the_local_artifact(store, tmp_path, monkeypatch):
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_artifact(tmp_path / "generated")))
    index = store.load_index()
    assert [hit.name for hit in index.search("blue_h")] == ["blue_hair"]
    assert [hit.name for hit in index.search("blu_h")] == ["blue_hair"]
    assert store.load_artifact() is store.load_artifact()


def test_status_is_missing_with_an_empty_cache(store):
    status = store.status()
    assert status.state == store.STATE_MISSING
    assert status.data_version is None
    assert status.error is None


def test_download_populates_the_cache_and_verifies_the_sha256(store, tmp_path, http_files, monkeypatch):
    root, base_url = http_files
    source = build_artifact(root)
    pointer = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    (root / "latest.json").write_text(
        json.dumps({
            "data_version": "2026.09.22",
            "profile": "danbooru",
            "sha256": pointer["artifact"]["sha256"],
            "size": pointer["artifact"]["size"],
            "url": f"{base_url}/tags.bin.gz",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DTA_LATEST_URL", f"{base_url}/latest.json")

    store.ensure_download()
    status = wait_for_state(store, store.STATE_READY)

    assert status.data_version == "2026.09.22"
    assert store.artifact_path().read_bytes() == (root / "tags.bin.gz").read_bytes()
    assert store.metadata_path().exists()
    assert store.load_index().search("blue_h")[0].name == "blue_hair"


def test_download_records_an_error_and_does_not_retry(store, tmp_path, http_files, monkeypatch):
    root, base_url = http_files
    source = build_artifact(root)
    (root / "latest.json").write_text(
        json.dumps({
            "data_version": "2026.09.22",
            "profile": "danbooru",
            "sha256": "0" * 64,
            "size": source.stat().st_size,
            "url": f"{base_url}/tags.bin.gz",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DTA_LATEST_URL", f"{base_url}/latest.json")

    store.ensure_download()
    status = wait_for_state(store, store.STATE_ERROR)
    assert "sha256 mismatch" in status.error
    assert not store.artifact_path().exists()

    store.ensure_download()
    assert store.status().state == store.STATE_ERROR


def test_download_failure_without_a_pointer_is_recorded_as_an_error(store, monkeypatch):
    monkeypatch.setenv("DTA_LATEST_URL", "http://127.0.0.1:1/latest.json")
    store.ensure_download()
    status = wait_for_state(store, store.STATE_ERROR)
    assert status.error


def test_artifact_content_encoding_detects_gzip(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    assert store.artifact_content_encoding() == "gzip"

    plain = tmp_path / "generated" / "tags.bin"
    plain.write_bytes(gzip.decompress(source.read_bytes()))
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(plain))
    assert store.artifact_content_encoding() is None


def test_custom_payload_is_absent_without_a_custom_file(store, tmp_path, monkeypatch):
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_artifact(tmp_path / "generated")))
    payload = store.custom_payload()
    assert payload == {"available": False, "warnings": [], "bytes": None}


def test_custom_payload_round_trips_the_overlay(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    (store.cache_dir() / "custom_tags.csv").write_text(
        "example_tag,general,0,\nmy_old_tag,general,0,blue_hair\n", encoding="utf-8"
    )

    payload = store.custom_payload()

    assert payload["available"] is True
    assert payload["warnings"] == []
    overlay_bytes = base64.b64decode(payload["bytes"])
    index = store.load_index()
    assert [hit.name for hit in index.search("my_old_tag")] == ["blue_hair"]
    assert overlay_bytes[:4] == b"DTA1"


def test_custom_payload_reports_a_broken_custom_file(store, tmp_path, monkeypatch):
    source = build_artifact(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    (store.cache_dir() / "custom_tags.csv").write_text("a,0,1,x,y\n", encoding="utf-8")

    payload = store.custom_payload()

    assert payload["available"] is False
    assert payload["warnings"]
    assert [hit.name for hit in store.load_index().search("blue_h")] == ["blue_hair"]
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dta_node.store'`

- [ ] **Step 5: Write `store.py`**

```python
"""Runtime store for the tag artifact and the user's custom tag file.

ComfyUI-facing: this module may use folder_paths and the ComfyUI user directory.
Nothing under build/ may import it.

There is no bundled tag data. The artifact is downloaded once into the ComfyUI
user directory and reused offline; `DTA_LOCAL_ARTIFACT` points it at a local build
output instead, which is how the project is developed and verified before a
release exists.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import requests

from .artifact import Artifact, TagIndex, build_custom_overlay, decode

CACHE_DIRNAME = "danbooru-tag-autocomplete"
ARTIFACT_FILENAME = "tags.bin.gz"
METADATA_FILENAME = "metadata.json"
CUSTOM_FILENAMES = ("custom_tags.csv", "custom_tags.json")
DEFAULT_REPO_SLUG = "chynggi/ComfyUI-Danbooru-Tag-Autocomplete"

LATEST_URL_ENV = "DTA_LATEST_URL"
REPO_SLUG_ENV = "DTA_REPO_SLUG"
LOCAL_ARTIFACT_ENV = "DTA_LOCAL_ARTIFACT"

STATE_READY = "ready"
STATE_DOWNLOADING = "downloading"
STATE_MISSING = "missing"
STATE_ERROR = "error"

log = logging.getLogger(__name__)

_lock = threading.Lock()
_state = STATE_MISSING
_error: str | None = None
_download_thread: threading.Thread | None = None
_artifact_cache: tuple[tuple[Path, int, int], Artifact] | None = None
_custom_cache: tuple[tuple[Path, int], tuple[Artifact | None, tuple[str, ...]]] | None = None


@dataclass(frozen=True, slots=True)
class Status:
    state: str
    data_version: str | None
    error: str | None


def cache_dir() -> Path:
    """Directory holding the cached artifact.

    folder_paths is imported here rather than at module scope so this module can be
    imported outside a running ComfyUI, which is what the tests rely on.
    """
    import folder_paths

    return Path(folder_paths.get_user_directory()) / CACHE_DIRNAME


def _local_artifact() -> Path | None:
    value = os.environ.get(LOCAL_ARTIFACT_ENV)
    return Path(value) if value else None


def artifact_path() -> Path:
    local = _local_artifact()
    if local is not None:
        return local
    return cache_dir() / ARTIFACT_FILENAME


def metadata_path() -> Path:
    local = _local_artifact()
    if local is not None:
        return local.with_name(METADATA_FILENAME)
    return cache_dir() / METADATA_FILENAME


def custom_file_path() -> Path | None:
    for name in CUSTOM_FILENAMES:
        candidate = cache_dir() / name
        if candidate.exists():
            return candidate
    return None


def _latest_url() -> str:
    override = os.environ.get(LATEST_URL_ENV)
    if override:
        return override
    slug = os.environ.get(REPO_SLUG_ENV, DEFAULT_REPO_SLUG)
    return f"https://raw.githubusercontent.com/{slug}/main/data/latest.json"


def _data_version() -> str | None:
    path = metadata_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("data_version")
    except (OSError, ValueError):
        return None


def status() -> Status:
    """Report the cache state. Never raises."""
    if artifact_path().exists() and metadata_path().exists():
        with _lock:
            if _state != STATE_DOWNLOADING:
                return Status(STATE_READY, _data_version(), None)
    with _lock:
        return Status(_state, _data_version(), _error)


def ensure_download() -> None:
    """Start a background download when the cache is empty.

    Idempotent and non-blocking. A failed attempt is not retried automatically:
    only a ComfyUI restart starts a new one, which keeps a broken endpoint from
    being hammered by the frontend's polling.
    """
    global _state, _error, _download_thread
    with _lock:
        ready = artifact_path().exists() and metadata_path().exists()
        if ready:
            _state = STATE_READY
            return
        if _download_thread is not None and _download_thread.is_alive():
            return
        if _state in (STATE_DOWNLOADING, STATE_ERROR):
            return
        _state = STATE_DOWNLOADING
        _error = None
        _download_thread = threading.Thread(target=_download, name="dta-download", daemon=True)
        _download_thread.start()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _download() -> None:
    global _state, _error
    try:
        pointer_response = requests.get(_latest_url(), timeout=30)
        pointer_response.raise_for_status()
        pointer = pointer_response.json()

        artifact_response = requests.get(pointer["url"], timeout=300)
        artifact_response.raise_for_status()
        payload = artifact_response.content

        expected = pointer.get("sha256")
        digest = hashlib.sha256(payload).hexdigest()
        if expected and digest != expected:
            raise ValueError(f"artifact sha256 mismatch (expected {expected}, got {digest})")

        metadata_response = requests.get(
            pointer["url"].rsplit("/", 1)[0] + "/" + METADATA_FILENAME, timeout=60
        )
        metadata_response.raise_for_status()
        metadata_response.json()

        _write_atomic(artifact_path(), payload)
        _write_atomic(metadata_path(), metadata_response.content)
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        log.warning("danbooru-tag-autocomplete: artifact download failed: %s", exc)
        with _lock:
            _state = STATE_ERROR
            _error = str(exc)
        return
    with _lock:
        _state = STATE_READY
        _error = None


def _read_artifact_bytes(path: Path) -> bytes:
    payload = path.read_bytes()
    if payload[:2] == b"\x1f\x8b":
        return gzip.decompress(payload)
    return payload


def load_artifact() -> Artifact:
    """Decode the artifact, cached until the file it came from changes."""
    global _artifact_cache
    path = artifact_path()
    stat = path.stat()
    identity = (path, stat.st_mtime_ns, stat.st_size)
    if _artifact_cache is not None and _artifact_cache[0] == identity:
        return _artifact_cache[1]
    artifact = decode(_read_artifact_bytes(path))
    _artifact_cache = (identity, artifact)
    return artifact


def load_custom() -> tuple[Artifact | None, tuple[str, ...]]:
    """Build the custom overlay for the user's custom file, cached by file identity."""
    global _custom_cache
    path = custom_file_path()
    if path is None:
        return None, ()
    stat = path.stat()
    identity = (path, stat.st_mtime_ns)
    if _custom_cache is not None and _custom_cache[0] == identity:
        return _custom_cache[1]
    try:
        result = build_custom_overlay(load_artifact(), path.read_text(encoding="utf-8"), path.name)
    except (OSError, ValueError) as exc:
        log.warning("danbooru-tag-autocomplete: %s could not be used: %s", path.name, exc)
        result = (None, (f"{path.name}: {exc}",))
    _custom_cache = (identity, result)
    return result


def load_index() -> TagIndex:
    """The index the node searches: the main artifact plus the custom overlay."""
    return TagIndex(load_artifact(), custom=load_custom()[0])


def artifact_content_encoding() -> str | None:
    path = artifact_path()
    if not path.exists():
        return None
    with open(path, "rb") as handle:
        return "gzip" if handle.read(2) == b"\x1f\x8b" else None


def custom_payload() -> dict:
    """JSON-safe custom overlay for the browser."""
    overlay, warnings = load_custom()
    return {
        "available": overlay is not None,
        "warnings": list(warnings),
        "bytes": base64.b64encode(overlay.to_bytes()).decode("ascii") if overlay is not None else None,
    }
```

- [ ] **Step 6: Run the store tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (10 passed)

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (115 + the new gates; 1 deselected only if `-m "not slow"` was used)

```bash
git add artifact.py store.py tests/conftest.py tests/test_store.py tests/test_artifact.py
git commit -m "Add runtime artifact store"
```

---

### Task 4: HTTP routes and package wiring

**Files:**
- Create: `routes.py`
- Create: `__init__.py`
- Create: `tests/test_routes.py`

**Interfaces:**
- Consumes: `store.status`, `store.ensure_download`, `store.artifact_path`, `store.artifact_content_encoding`, `store.custom_payload`, `store.STATE_MISSING`
- Produces:
  - `routes.register_routes(app_routes) -> None`
  - three aiohttp handlers registered on `PromptServer.instance.routes` at import: `GET /danbooru-tag-autocomplete/status`, `GET /danbooru-tag-autocomplete/db`, `GET /danbooru-tag-autocomplete/custom`
  - `/status` JSON: `{"state": str, "dataVersion": str | null, "error": str | null}`; kicks off a download when the state is `missing`
  - `/db`: the artifact bytes with `Content-Type: application/octet-stream`, `Content-Encoding: gzip` when the file is gzipped, `Cache-Control: no-cache`, or a 404 JSON `{"error": str}`
  - `/custom`: the `store.custom_payload()` JSON
  - `__init__.py` exports `WEB_DIRECTORY = "./web"`, `NODE_CLASS_MAPPINGS`, `NODE_DISPLAY_NAME_MAPPINGS`

`register_routes(app_routes)` takes the route table as a parameter so the tests can pass a
recorder instead of `PromptServer.instance.routes`.

- [ ] **Step 1: Install the test-only HTTP dependency and write the failing route tests**

Run: `uv pip install --python .venv/bin/python aiohttp`

The route tests stub `server` so they can exercise the handlers without a running
ComfyUI. `aiohttp` is a ComfyUI runtime dependency; installing it in this project's venv
is test-only.

Create `tests/test_routes.py`:

```python
import importlib
import json
import sys
import types

import pytest

from artifact import TagEntry, TagSet, encode


class RouteRecorder:
    def __init__(self):
        self.handlers = {}

    def _register(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler

        return decorator

    def get(self, path):
        return self._register("GET", path)


class FakeRequest:
    pass


@pytest.fixture
def routes(node_package, monkeypatch):
    recorder = RouteRecorder()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=recorder))
    monkeypatch.setitem(sys.modules, "server", server)
    module = importlib.import_module(f"{node_package}.routes")
    return module, recorder


def build_source(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tags.bin.gz"
    import gzip

    raw = encode(TagSet(threshold=25, tags=(TagEntry("1girl", 0, 10, False),), aliases=(), alias_target=()))
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(raw)
    (directory / "metadata.json").write_text(json.dumps({"data_version": "2026.09.22"}), encoding="utf-8")
    return path


def test_all_routes_are_registered(routes):
    _, recorder = routes
    assert set(recorder.handlers) == {
        ("GET", "/danbooru-tag-autocomplete/status"),
        ("GET", "/danbooru-tag-autocomplete/db"),
        ("GET", "/danbooru-tag-autocomplete/custom"),
    }


def test_status_reports_ready_with_a_local_artifact(routes, tmp_path, monkeypatch):
    module, recorder = routes
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_source(tmp_path / "generated")))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/status")](FakeRequest())
    )

    assert response.status == 200
    assert json.loads(response.body) == {"state": "ready", "dataVersion": "2026.09.22", "error": None}


def test_db_serves_the_artifact_with_gzip_encoding(routes, tmp_path, monkeypatch):
    module, recorder = routes
    source = build_source(tmp_path / "generated")
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/db")](FakeRequest())
    )

    assert response.status == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert response.headers["Cache-Control"] == "no-cache"


def test_db_returns_404_without_an_artifact(routes, tmp_path, monkeypatch):
    module, recorder = routes
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(tmp_path / "generated" / "tags.bin.gz"))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/db")](FakeRequest())
    )

    assert response.status == 404
    assert "error" in json.loads(response.body)


def test_custom_returns_the_overlay_payload(routes, tmp_path, monkeypatch):
    module, recorder = routes
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_source(tmp_path / "generated")))

    response = importlib.import_module("asyncio").run(
        recorder.handlers[("GET", "/danbooru-tag-autocomplete/custom")](FakeRequest())
    )

    assert response.status == 200
    assert json.loads(response.body) == {"available": False, "warnings": [], "bytes": None}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dta_node.routes'`

- [ ] **Step 3: Write `routes.py`**

```python
"""HTTP routes serving the tag artifact to the browser."""

from __future__ import annotations

from aiohttp import web
from server import PromptServer

from . import store

PREFIX = "/danbooru-tag-autocomplete"


async def status_route(request: web.Request) -> web.Response:
    current = store.status()
    if current.state == store.STATE_MISSING:
        store.ensure_download()
        current = store.status()
    return web.json_response(
        {"state": current.state, "dataVersion": current.data_version, "error": current.error}
    )


async def db_route(request: web.Request) -> web.Response:
    path = store.artifact_path()
    if not path.exists():
        return web.json_response({"error": "tag database is not available"}, status=404)
    response = web.FileResponse(path)
    response.headers["Content-Type"] = "application/octet-stream"
    response.headers["Cache-Control"] = "no-cache"
    encoding = store.artifact_content_encoding()
    if encoding is not None:
        response.headers["Content-Encoding"] = encoding
    return response


async def custom_route(request: web.Request) -> web.Response:
    return web.json_response(store.custom_payload())


def register_routes(app_routes) -> None:
    app_routes.get(f"{PREFIX}/status")(status_route)
    app_routes.get(f"{PREFIX}/db")(db_route)
    app_routes.get(f"{PREFIX}/custom")(custom_route)


register_routes(PromptServer.instance.routes)
```

- [ ] **Step 4: Write `__init__.py`**

```python
"""Danbooru tag autocomplete for ComfyUI."""

from __future__ import annotations

from .nodes import DanbooruTagSearch

WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {"DanbooruTagSearch": DanbooruTagSearch}
NODE_DISPLAY_NAME_MAPPINGS = {"DanbooruTagSearch": "Danbooru Tag Search"}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

from . import routes  # noqa: E402,F401  registers the HTTP routes on import
```

- [ ] **Step 5: Run the route tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Verify the package imports the way ComfyUI loads it**

Run:
```bash
.venv/bin/python - <<'PY'
import importlib.util
import sys
import types

root = "."
package = types.ModuleType("dta_probe")
package.__path__ = [root]
sys.modules["dta_probe"] = package
server = types.ModuleType("server")
server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=types.SimpleNamespace(get=lambda path: lambda handler: handler)))
sys.modules["server"] = server
folder_paths = types.ModuleType("folder_paths")
folder_paths.get_user_directory = lambda: "/tmp"
sys.modules["folder_paths"] = folder_paths

module = importlib.import_module("dta_probe")
print("WEB_DIRECTORY", module.WEB_DIRECTORY)
print("NODES", sorted(module.NODE_CLASS_MAPPINGS))
print("DISPLAY", list(module.NODE_DISPLAY_NAME_MAPPINGS.values()))
PY
```
Expected: prints `WEB_DIRECTORY ./web`, `NODES ['DanbooruTagSearch']`, `DISPLAY ['Danbooru Tag Search']`

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add routes.py __init__.py tests/test_routes.py
git commit -m "Add tag database HTTP routes"
```

---

### Task 5: The `Danbooru Tag Search` node

**Files:**
- Create: `nodes.py`
- Create: `tests/test_nodes.py`

**Interfaces:**
- Consumes: `store.load_index`, `artifact.SearchHit`, `artifact.RANK_NAME_PREFIX`
- Produces: `nodes.DanbooruTagSearch` with
  - `INPUT_TYPES()` → `query` (STRING), `category` (combo of `any/general/artist/copyright/character/meta`), `min_post_count` (INT, 0), `limit` (INT, 1..200, default 32), `sort` (combo of `relevance/post_count/name`), `exclude_deprecated` (BOOLEAN, default True)
  - `RETURN_TYPES = ("STRING",)`, `RETURN_NAMES = ("tags",)`, `FUNCTION = "search"`, `CATEGORY = "Danbooru"`
  - `search(query, category, min_post_count, limit, sort, exclude_deprecated) -> tuple[str]` — a comma-and-space joined tag list

`query` accepts one term or several separated by `,`, `;` or newlines, so a whole prompt
can be pasted in. Each term is searched independently and the results are merged, then
re-sorted globally by the chosen order. Alias and deprecated matches always report the
canonical tag name.

- [ ] **Step 1: Write the failing node tests**

Create `tests/test_nodes.py`:

```python
import gzip
import importlib
import json
from pathlib import Path

import pytest

from artifact import TagEntry, TagSet, encode


def build_source(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tags.bin.gz"
    raw = encode(TagSet(
        threshold=25,
        tags=(
            TagEntry("1girl", 0, 5000, False),
            TagEntry("blue_hair", 0, 1200, False),
            TagEntry("blue_hairband", 0, 1500, False),
            TagEntry("hatsune_miku", 4, 900, False),
            TagEntry("old_tag", 0, 10, True),
        ),
        aliases=("blu_hair",),
        alias_target=(1,),
    ))
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(raw)
    (directory / "metadata.json").write_text(json.dumps({"data_version": "2026.09.22"}), encoding="utf-8")
    return path


@pytest.fixture
def node(node_package, tmp_path, monkeypatch):
    module = importlib.import_module(f"{node_package}.nodes")
    store = importlib.import_module(f"{node_package}.store")
    monkeypatch.setattr(store, "_artifact_cache", None, raising=False)
    monkeypatch.setattr(store, "_custom_cache", None, raising=False)
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_source(tmp_path / "generated")))
    return module.DanbooruTagSearch()


def test_relevance_orders_by_rank_then_name_length(node):
    assert node.search("blue_h", "any", 0, 32, "relevance", True)[0] == "blue_hair, blue_hairband"


def test_post_count_sort_orders_by_count(node):
    assert node.search("blue_h", "any", 0, 32, "post_count", True)[0] == "blue_hairband, blue_hair"


def test_name_sort_orders_alphabetically(node):
    assert node.search("blue_h", "any", 0, 32, "name", True)[0] == "blue_hair, blue_hairband"


def test_min_post_count_filters_results(node):
    assert node.search("blue_h", "any", 1300, 32, "relevance", True)[0] == "blue_hairband"


def test_limit_caps_the_result(node):
    assert node.search("blue_h", "any", 0, 1, "relevance", True)[0] == "blue_hair"


def test_category_filters_results(node):
    assert node.search("hatsune_miku", "character", 0, 32, "relevance", True)[0] == "hatsune_miku"
    assert node.search("hatsune_miku", "general", 0, 32, "relevance", True)[0] == ""


def test_alias_query_returns_the_canonical_tag(node):
    assert node.search("blu_h", "any", 0, 32, "relevance", True)[0] == "blue_hair"


def test_deprecated_matches_are_excluded_unless_requested(node):
    assert node.search("old_tag", "any", 0, 32, "relevance", True)[0] == ""
    assert node.search("old_tag", "any", 0, 32, "relevance", False)[0] == "old_tag"


def test_multiple_terms_are_merged_and_deduplicated(node):
    assert node.search("1girl, blue_h", "any", 0, 32, "relevance", True)[0] == "1girl, blue_hair, blue_hairband"


def test_newline_separated_terms_are_split(node):
    assert node.search("1girl\nblue_h", "any", 0, 32, "relevance", True)[0] == "1girl, blue_hair, blue_hairband"


def test_empty_query_returns_an_empty_string(node):
    assert node.search("", "any", 0, 32, "relevance", True)[0] == ""
    assert node.search("   ", "any", 0, 32, "relevance", True)[0] == ""


def test_a_missing_database_returns_an_empty_string(node, tmp_path, monkeypatch):
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(tmp_path / "nowhere" / "tags.bin.gz"))
    assert node.search("blue_h", "any", 0, 32, "relevance", True)[0] == ""


def test_input_types_expose_the_documented_options(node):
    required = node.INPUT_TYPES()["required"]
    assert list(required) == ["query", "category", "min_post_count", "limit", "sort", "exclude_deprecated"]
    assert list(required["category"][0]) == ["any", "general", "artist", "copyright", "character", "meta"]
    assert list(required["sort"][0]) == ["relevance", "post_count", "name"]
    assert node.RETURN_TYPES == ("STRING",)
    assert node.CATEGORY == "Danbooru"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_nodes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dta_node.nodes'`

- [ ] **Step 3: Write `nodes.py`**

```python
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
```

- [ ] **Step 4: Run the node tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_nodes.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q && node --test tests/test_search_js.mjs`
Expected: PASS (both)

```bash
git add nodes.py tests/test_nodes.py
git commit -m "Add Danbooru Tag Search node"
```

---

## M2a completion criteria

- `.venv/bin/python -m pytest -q` passes, including the three `slow` latency gates.
- `node --test tests/test_search_js.mjs` passes.
- The three benchmark lines report the full-size decode, the shipped-profile short-prefix
  p95, and the real-artifact short-prefix p95; all are inside their budgets. If any was
  not, it is reported rather than absorbed by raising a budget.
- `tests/fixtures/queries.json` carries `nameLength` and the Python fixture test compares
  every field, so the browser port has a field-complete contract.
- `DTA_LOCAL_ARTIFACT=generated/tags.bin.gz` makes `store.status()` report `ready` and
  `store.load_index().search("blue_h")` return the expected hit without any network.
- No file outside `custom_nodes/ComfyUI-Danbooru-Tag-Autocomplete` was modified.

## M2b and M3

- **M2b** (browser UX) consumes `web/search.js`, the `/danbooru-tag-autocomplete/*` routes
  and `tests/fixtures/queries.json`. It adds `web/caret.js`, `web/dropdown.js`,
  `web/dtautocomplete.js`, the ComfyUI settings, and the manual browser checklist.
- **M3** adds the update-data workflow, the remaining profiles and the full README, and
  publishes the release asset that makes the `/status` → download path live end to end.
  Its workflow must run the pytest suite after the build so the real-artifact latency gate
  and the real-artifact validation gate both run against freshly built output.
