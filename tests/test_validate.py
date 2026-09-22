import gzip
import hashlib
import json
import struct

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
    assert any("invalid category" in error for error in validate(path))


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


def test_truncated_gzip_is_reported_not_raised(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    path.write_bytes(path.read_bytes()[:-8])
    errors = validate(path)
    assert errors and "could not be read" in errors[0]


def test_corrupt_gzip_body_is_reported_not_raised(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    assert validate(path)


def test_malformed_metadata_is_reported_not_raised(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{not json", encoding="utf-8")
    assert any("metadata could not be read" in error for error in validate(path, metadata))


def test_metadata_that_is_not_an_object_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    metadata = tmp_path / "metadata.json"
    metadata.write_text("[1, 2, 3]", encoding="utf-8")
    assert any("not a JSON object" in error for error in validate(path, metadata))


def test_matching_metadata_passes(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    write_metadata(
        tmp_path,
        counts={"tags": 1, "aliases": 0, "deprecated": 0},
        artifact={"file": "tags.bin.gz", "sha256": digest, "size": path.stat().st_size, "raw_size": 0},
    )
    assert validate(path, tmp_path / "metadata.json") == []


def test_metadata_count_mismatch_is_reported(tmp_path):
    path = write_artifact(tmp_path, [TagEntry("a", 0, 10, False)])
    write_metadata(tmp_path, counts={"tags": 99, "aliases": 0, "deprecated": 0})
    assert any("counts.tags" in error for error in validate(path, tmp_path / "metadata.json"))


def test_metadata_deprecated_count_mismatch_is_reported(tmp_path):
    tags = [TagEntry("a", 0, 10, False), TagEntry("b", 0, 10, True)]
    path = write_artifact(tmp_path, tags)
    write_metadata(tmp_path, counts={"tags": 2, "aliases": 0, "deprecated": 0})
    assert any("counts.deprecated" in error for error in validate(path, tmp_path / "metadata.json"))


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


def test_validate_custom_reports_an_undecodable_file(tmp_path):
    path = tmp_path / "custom_tags.csv"
    path.write_bytes(b"\xff\xfe\x00a,0,1,\n")
    assert validate_custom(path)


def test_validate_custom_reports_a_non_object_json_entry(tmp_path):
    path = tmp_path / "custom_tags.json"
    path.write_text('[{"tag": "a", "alias": 5}]', encoding="utf-8")
    assert validate_custom(path)


def test_valid_categories_matches_danbooru():
    assert VALID_CATEGORIES == frozenset({0, 1, 3, 4, 5})


def test_invalid_utf8_name_is_reported_not_raised(tmp_path):
    blob = bytearray(encode(TagSet(
        threshold=25,
        tags=(TagEntry("a", 0, 10, False),),
        aliases=(),
        alias_target=(),
    )))
    off_names = struct.unpack_from("<I", blob, 20)[0]
    blob[off_names:off_names + 1] = b"\xff"
    path = tmp_path / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(blob)
    assert any("not valid UTF-8" in error for error in validate(path))


def test_invalid_utf8_alias_is_reported_not_raised(tmp_path):
    blob = bytearray(encode(TagSet(
        threshold=25,
        tags=(TagEntry("a", 0, 10, False),),
        aliases=("b",),
        alias_target=(0,),
    )))
    off_alias_names = struct.unpack_from("<I", blob, 40)[0]
    blob[off_alias_names:off_alias_names + 1] = b"\xff"
    path = tmp_path / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(blob)
    assert any("not valid UTF-8" in error for error in validate(path))
