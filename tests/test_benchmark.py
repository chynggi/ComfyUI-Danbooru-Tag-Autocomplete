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
REAL_ARTIFACT = Path(__file__).resolve().parents[1] / "generated" / "tags.bin.gz"


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
