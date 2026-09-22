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
