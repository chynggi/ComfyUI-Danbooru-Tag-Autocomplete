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
