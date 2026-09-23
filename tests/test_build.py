import hashlib
import json

import pytest

from artifact import decode, encode, normalize_tag
from build.build_database import Profile, assemble, load_profile, merge_sources, resolve_aliases, write_artifacts, write_latest
from build.sources.base import SourceData, TagRecord

FIXTURE_PROFILE = "profiles/danbooru.yaml"


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


def test_main_removes_outputs_when_validation_fails(tmp_path, monkeypatch):
    from build import build_database

    class StubSource:
        def read(self, fetched):
            return hlibr_data()

    monkeypatch.setattr(build_database, "fetch_all", lambda names, cache: [object()])
    monkeypatch.setattr(build_database, "SOURCE_ORDER", ("hlibr",))
    monkeypatch.setattr(build_database, "SOURCES", {"hlibr": StubSource})
    monkeypatch.setattr(build_database, "validate", lambda artifact, metadata: ["synthetic failure"])

    out = tmp_path / "generated"
    latest = tmp_path / "data" / "latest.json"
    code = build_database.main([
        "--profile", str(FIXTURE_PROFILE),
        "--out", str(out),
        "--latest-json", str(latest),
        "--repo-slug", "owner/name",
    ])

    assert code == 1
    assert not (out / "tags.bin.gz").exists()
    assert not (out / "metadata.json").exists()
    assert not latest.exists()


LATEST_SHA = "d4" * 32

LATEST_METADATA = {
    "data_version": "2026.09.22",
    "profile": "danbooru",
    "artifact": {"sha256": LATEST_SHA, "size": 2315906},
}


def test_write_latest_records_the_release_pointer(tmp_path):
    path = write_latest(LATEST_METADATA, tmp_path / "latest.json", "owner/name")

    # The exact serialization, not only the parsed value. The update workflow decides whether to
    # publish by diffing this file, so the key order and the indentation are part of the contract:
    # a reorder that leaves the parsed value identical would still make every build look changed.
    expected = "\n".join([
        "{",
        '  "data_version": "2026.09.22",',
        '  "profile": "danbooru",',
        f'  "sha256": "{LATEST_SHA}",',
        '  "size": 2315906,',
        '  "url": "https://github.com/owner/name/releases/download/data-2026.09.22/tags.bin.gz"',
        "}",
        "",
    ])
    assert path.read_text(encoding="utf-8") == expected


def test_write_latest_is_byte_identical_for_the_same_build(tmp_path):
    # The same build has to produce the same bytes even if one value becomes nondeterministic.
    first = write_latest(LATEST_METADATA, tmp_path / "a.json", "owner/name").read_bytes()
    second = write_latest(LATEST_METADATA, tmp_path / "b.json", "owner/name").read_bytes()

    assert first == second


def test_main_writes_the_latest_pointer(tmp_path, monkeypatch):
    from build import build_database

    class StubSource:
        def read(self, fetched):
            return hlibr_data()

    monkeypatch.setattr(build_database, "fetch_all", lambda names, cache: [object()])
    monkeypatch.setattr(build_database, "SOURCE_ORDER", ("hlibr",))
    monkeypatch.setattr(build_database, "SOURCES", {"hlibr": StubSource})

    out = tmp_path / "generated"
    latest = tmp_path / "data" / "latest.json"
    code = build_database.main([
        "--profile", str(FIXTURE_PROFILE),
        "--out", str(out),
        "--latest-json", str(latest),
        "--repo-slug", "owner/name",
    ])

    assert code == 0
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["data_version"] == metadata["data_version"]
    assert payload["profile"] == metadata["profile"]
    assert payload["sha256"] == metadata["artifact"]["sha256"]
    assert payload["size"] == metadata["artifact"]["size"]
    assert payload["url"] == (
        f"https://github.com/owner/name/releases/download/data-{payload['data_version']}/tags.bin.gz"
    )
