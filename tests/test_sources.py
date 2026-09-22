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
        "2026-04-08",
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
