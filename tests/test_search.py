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


import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_shared_fixture_matches_python_search():
    data = (FIXTURES / "artifact.bin").read_bytes()
    expected = json.loads((FIXTURES / "queries.json").read_text(encoding="utf-8"))
    index = TagIndex(artifact.decode(data))
    for query, hits in expected["queries"].items():
        assert [hit.name for hit in index.search(query)] == [hit["name"] for hit in hits], query
