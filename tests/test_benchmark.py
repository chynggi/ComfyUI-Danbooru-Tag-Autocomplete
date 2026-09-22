"""Latency checks against a full-size synthetic artifact (1,710,000 tags).

Run with: .venv/bin/python -m pytest tests/test_benchmark.py -m slow -v
Skip in fast local runs with: .venv/bin/python -m pytest -m "not slow"
"""

import time

import pytest

from artifact import Artifact, TagEntry, TagIndex, TagSet, decode, encode

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
