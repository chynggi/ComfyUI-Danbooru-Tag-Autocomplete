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


def test_astral_names_tie_break_by_code_point_not_utf16_unit():
    index = TagIndex(artifact.decode((FIXTURES / "artifact.bin").read_bytes()))
    assert [hit.name for hit in index.search("x")] == ["x\ue000x", "x\U00010000"]
