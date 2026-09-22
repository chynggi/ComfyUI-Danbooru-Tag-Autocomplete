"""Fetch upstream tag datasets into a local raw cache.

Standalone CLI for prefetching and debugging. build_database.py calls fetch_all()
directly, so this script is not required for a build.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources.base import FetchResult  # noqa: E402
from sources.hdiffusion import HDiffusionSource  # noqa: E402
from sources.hlibr import HlibrSource  # noqa: E402

SOURCES = {"hlibr": HlibrSource, "hdiffusion": HDiffusionSource}
SOURCE_ORDER = ("hlibr", "hdiffusion")


def fetch_all(names: list[str], cache_dir: Path) -> list[FetchResult]:
    results = []
    for name in names:
        if name not in SOURCES:
            raise ValueError(f"unknown source {name!r}; available: {sorted(SOURCES)}")
        results.append(SOURCES[name]().fetch(cache_dir))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch upstream Danbooru tag datasets")
    parser.add_argument("--source", action="append", dest="sources", help="source name; repeatable")
    parser.add_argument("--cache", default="data/raw", help="raw download cache directory")
    parser.add_argument("--manifest", default="generated/raw/manifest.json")
    args = parser.parse_args(argv)

    names = args.sources or list(SOURCE_ORDER)
    results = fetch_all(names, Path(args.cache))
    manifest = {
        result.source_id: {
            "revision": result.revision,
            "data_date": result.data_date,
            "files": {key: str(value) for key, value in result.files.items()},
            "hashes": result.hashes,
        }
        for result in results
    }
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for source_id, info in manifest.items():
        print(f"{source_id}: revision={info['revision'][:12]} data_date={info['data_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
