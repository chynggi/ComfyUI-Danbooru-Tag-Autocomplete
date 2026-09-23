import gzip
import importlib
import json
from pathlib import Path

import pytest

from artifact import TagEntry, TagSet, encode


def build_source(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tags.bin.gz"
    raw = encode(TagSet(
        threshold=25,
        tags=(
            TagEntry("1girl", 0, 5000, False),
            TagEntry("blue_hair", 0, 1200, False),
            TagEntry("blue_hairband", 0, 1500, False),
            TagEntry("hatsune_miku", 4, 900, False),
            TagEntry("old_tag", 0, 10, True),
        ),
        aliases=("blu_hair",),
        alias_target=(1,),
    ))
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(raw)
    (directory / "metadata.json").write_text(json.dumps({"data_version": "2026.09.22"}), encoding="utf-8")
    return path


@pytest.fixture
def node(node_package, tmp_path, monkeypatch):
    module = importlib.import_module(f"{node_package}.nodes")
    store = importlib.import_module(f"{node_package}.store")
    monkeypatch.setattr(store, "_artifact_cache", None)
    monkeypatch.setattr(store, "_custom_cache", None)
    monkeypatch.setattr(store, "cache_dir", lambda: tmp_path)
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(build_source(tmp_path / "generated")))
    return module.DanbooruTagSearch()


def test_relevance_orders_by_rank_then_name_length(node):
    assert node.search("blue_h", "any", 32, True)[0] == "blue_hair, blue_hairband"


def test_limit_caps_the_result(node):
    assert node.search("blue_h", "any", 1, True)[0] == "blue_hair"


def test_category_filters_results(node):
    assert node.search("hatsune_miku", "character", 32, True)[0] == "hatsune_miku"
    assert node.search("hatsune_miku", "general", 32, True)[0] == ""


def test_alias_query_returns_the_canonical_tag(node):
    assert node.search("blu_h", "any", 32, True)[0] == "blue_hair"


def test_deprecated_matches_are_excluded_unless_requested(node):
    assert node.search("old_tag", "any", 32, True)[0] == ""
    assert node.search("old_tag", "any", 32, False)[0] == "old_tag"


def test_multiple_terms_are_merged_and_deduplicated(node):
    assert node.search("1girl, blue_h", "any", 32, True)[0] == "1girl, blue_hair, blue_hairband"


def test_newline_separated_terms_are_split(node):
    assert node.search("1girl\nblue_h", "any", 32, True)[0] == "1girl, blue_hair, blue_hairband"


def test_term_order_does_not_change_the_result(node):
    forwards = node.search("blu_hair\nblue_h", "any", 32, True)[0]
    backwards = node.search("blue_h\nblu_hair", "any", 32, True)[0]
    assert forwards == backwards == "blue_hair, blue_hairband"


def test_empty_query_returns_an_empty_string(node):
    assert node.search("", "any", 32, True)[0] == ""
    assert node.search("   ", "any", 32, True)[0] == ""


def test_a_missing_database_returns_an_empty_string(node, tmp_path, monkeypatch):
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(tmp_path / "nowhere" / "tags.bin.gz"))
    assert node.search("blue_h", "any", 32, True)[0] == ""


def test_a_truncated_database_returns_an_empty_string(node, tmp_path, monkeypatch):
    source = build_source(tmp_path / "broken")
    source.write_bytes(source.read_bytes()[:-8])
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    assert node.search("blue_h", "any", 32, True)[0] == ""


def test_input_types_expose_the_documented_options(node):
    required = node.INPUT_TYPES()["required"]
    assert list(required) == ["query", "category", "limit", "exclude_deprecated"]
    assert list(required["category"][0]) == ["any", "general", "artist", "copyright", "character", "meta"]
    assert node.RETURN_TYPES == ("STRING",)
    assert node.RETURN_NAMES == ("tags",)
    assert node.CATEGORY == "Danbooru"
