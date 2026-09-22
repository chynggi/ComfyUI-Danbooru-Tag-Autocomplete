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
