import gzip
import json

from artifact import TagEntry, TagSet, VALID_CATEGORIES, encode
from build.validate_database import read_artifact, validate, validate_custom


def write_artifact(directory, tags, aliases=(), alias_target=()):
    blob = encode(TagSet(threshold=25, tags=tuple(tags), aliases=tuple(aliases), alias_target=tuple(alias_target)))
    path = directory / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(blob)
    return path


def write_metadata(directory, **overrides):
    payload = {
        "format_version": 1,
        "data_version": "2026.09.22",
        "profile": "danbooru",
        "threshold": 25,
        "counts": {"tags": 1, "aliases": 0, "deprecated": 0},
        "sources": [],
        "artifact": {"file": "tags.bin.gz", "sha256": "", "size": 0, "raw_size": 0},
    }
    payload.update(overrides)
    path = directory / "metadata.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_artifact_passes(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    assert validate(path) == []


def test_gzip_and_plain_buffers_are_both_accepted(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    plain = tmp_path / "tags.bin"
    plain.write_bytes(gzip.decompress(path.read_bytes()))
    assert read_artifact(path) == read_artifact(plain)


def test_invalid_category_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 2, 10, False)])
    errors = validate(path)
    assert any("invalid category" in error for error in errors)


def test_empty_name_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry(" ", 0, 10, False)])
    assert any("empty name" in error for error in validate(path))


def test_implausible_post_count_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 60_000_000, False)])
    assert any("implausible post_count" in error for error in validate(path))


def test_alias_name_matching_a_tag_name_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 1, False)], aliases=("a",), alias_target=(0,))
    assert any("also a tag name" in error for error in validate(path))


def test_alias_pointing_at_deprecated_target_is_reported(tmp_path):
    tags = [TagEntry("a", 0, 1, False), TagEntry("old", 0, 1, True)]
    path = write_artifact(tmp_path, tags, aliases=("legacy",), alias_target=(1,))
    assert any("target is deprecated" in error for error in validate(path))


def test_metadata_count_mismatch_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    write_metadata(tmp_path, counts={"tags": 99, "aliases": 0, "deprecated": 0})
    assert any("counts.tags" in error for error in validate(path, tmp_path / "metadata.json"))


def test_metadata_threshold_mismatch_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    write_metadata(tmp_path, threshold=99)
    assert any("threshold" in error for error in validate(path, tmp_path / "metadata.json"))


def test_metadata_sha_mismatch_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    write_metadata(
        tmp_path,
        artifact={"file": "tags.bin.gz", "sha256": "deadbeef", "size": 0, "raw_size": 0},
    )
    assert any("sha256" in error for error in validate(path, tmp_path / "metadata.json"))


def test_corrupt_artifact_reports_decode_failure(tmp_path):
    path = tmp_path / "tags.bin.gz"
    path.write_bytes(b"not an artifact")
    errors = validate(path)
    assert errors and "decode failed" in errors[0]


def test_validate_custom_reports_unknown_alias_target(tmp_path):
    path = tmp_path / "custom_tags.csv"
    path.write_text("a,0,1,missing\n", encoding="utf-8")
    assert any("unknown alias target" in warning for warning in validate_custom(path))


def test_validate_custom_reports_parse_errors(tmp_path):
    path = tmp_path / "custom_tags.csv"
    path.write_text("a,0,1,x,y\n", encoding="utf-8")
    assert any("expected 4 columns" in warning for warning in validate_custom(path))


def test_valid_categories_matches_danbooru():
    assert VALID_CATEGORIES == frozenset({0, 1, 3, 4, 5})
