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
