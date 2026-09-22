import json
from pathlib import Path

import pytest

from build.sources.base import FetchResult
from build.sources.hlibr import HlibrSource


def write_hlibr_fixture(directory: Path) -> FetchResult:
    pyarrow = pytest.importorskip("pyarrow")
    import pyarrow.parquet as parquet

    parquet.write_table(
        pyarrow.table({
            "name": ["Blue_Hair", "old_tag", "1girl"],
            "category": [0, 0, 0],
            "post_count": [1200, 7, 8000000],
            "is_deprecated": [False, True, False],
        }),
        directory / "tags.parquet",
    )
    parquet.write_table(
        pyarrow.table({
            "antecedent_name": ["blu_hair", "deleted_alias"],
            "consequent_name": ["blue_hair", "1girl"],
            "status": ["active", "deleted"],
        }),
        directory / "tag_aliases.parquet",
    )
    (directory / "metadata.json").write_text(
        json.dumps({"snapshot_built_at": "2026-04-08T09:15:30Z"}), encoding="utf-8"
    )
    return FetchResult(
        HlibrSource.id,
        "revision-sha",
        HlibrSource.data_date(directory / "metadata.json"),
        {
            "tags.parquet": directory / "tags.parquet",
            "tag_aliases.parquet": directory / "tag_aliases.parquet",
            "metadata.json": directory / "metadata.json",
        },
    )


def test_hlibr_normalizes_names_and_reads_deprecated(tmp_path):
    data = HlibrSource().read(write_hlibr_fixture(tmp_path))
    assert set(data.tags) == {"blue_hair", "old_tag", "1girl"}
    assert data.tags["old_tag"].is_deprecated is True
    assert data.tags["1girl"].post_count == 8000000
    assert data.data_date == "2026-04-08"


def test_hlibr_keeps_only_active_aliases(tmp_path):
    data = HlibrSource().read(write_hlibr_fixture(tmp_path))
    assert data.aliases == {"blu_hair": "blue_hair"}


def test_hlibr_missing_tag_column_raises(tmp_path):
    pyarrow = pytest.importorskip("pyarrow")
    import pyarrow.parquet as parquet

    parquet.write_table(pyarrow.table({"name": ["a"], "category": [0]}), tmp_path / "tags.parquet")
    parquet.write_table(
        pyarrow.table({"antecedent_name": ["b"], "consequent_name": ["a"], "status": ["active"]}),
        tmp_path / "tag_aliases.parquet",
    )
    fetched = FetchResult(
        HlibrSource.id,
        "rev",
        "2026-04-08",
        {"tags.parquet": tmp_path / "tags.parquet", "tag_aliases.parquet": tmp_path / "tag_aliases.parquet"},
    )
    with pytest.raises(ValueError, match="missing columns"):
        HlibrSource().read(fetched)


from build.sources.hdiffusion import HDiffusionSource


def write_hdiffusion_fixture(directory: Path, text: str) -> FetchResult:
    path = directory / "danbooru-2026-09-22.csv"
    path.write_text(text, encoding="utf-8")
    return FetchResult(HDiffusionSource.id, "revision-sha", "2026-09-22", {"csv": path})


def test_hdiffusion_reads_headerless_csv(tmp_path):
    fetched = write_hdiffusion_fixture(
        tmp_path,
        '1girl,0,8446417,"sole_female,1girls"\nhighres,5,8198922,"high_res,high_resolution"\n',
    )
    data = HDiffusionSource().read(fetched)
    assert set(data.tags) == {"1girl", "highres"}
    assert data.tags["1girl"].post_count == 8446417
    assert data.tags["highres"].category == 5
    assert data.tags["1girl"].aliases == ("sole_female", "1girls")
    assert data.aliases == {"sole_female": "1girl", "1girls": "1girl", "high_res": "highres", "high_resolution": "highres"}


def test_hdiffusion_latest_entry_picks_newest_date():
    entries = [
        {"path": "danbooru-2026-09-20.csv"},
        {"path": "danbooru-2026-09-22.csv"},
        {"path": "README.md"},
        {"path": "danbooru-2026-09-21.csv"},
    ]
    assert HDiffusionSource().latest_entry(entries) == ("2026-09-22", "danbooru-2026-09-22.csv")


def test_hdiffusion_latest_entry_raises_without_matches():
    with pytest.raises(RuntimeError, match="no danbooru-"):
        HDiffusionSource().latest_entry([{"path": "README.md"}])


def test_hdiffusion_rejects_row_with_too_few_columns(tmp_path):
    with pytest.raises(ValueError, match="at least 3 columns"):
        HDiffusionSource().read(write_hdiffusion_fixture(tmp_path, "broken,0\n"))


def test_hdiffusion_rejects_numeric_columns_that_are_not_numbers(tmp_path):
    with pytest.raises(ValueError):
        HDiffusionSource().read(write_hdiffusion_fixture(tmp_path, "tag,category,count\n"))
